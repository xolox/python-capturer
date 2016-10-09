# Easily capture stdout/stderr of the current process and subprocesses.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: October 9, 2016
# URL: https://capturer.readthedocs.org

# Standard library modules.
import multiprocessing
import os
import pty
import shutil
import signal
import sys
import tempfile
import time

# External dependencies.
from humanfriendly.terminal import clean_terminal_output

interpret_carriage_returns = clean_terminal_output
"""
Alias to :func:`humanfriendly.terminal.clean_terminal_output()`.

In `capturer` version 2.1.2 the ``interpret_carriage_returns()`` function was
obsoleted by :func:`humanfriendly.terminal.clean_terminal_output()`. This alias
remains for backwards compatibility.
"""

DEFAULT_TEXT_ENCODING = 'UTF-8'
"""
The name of the default character encoding used to convert captured output to
Unicode text (a string).
"""

GRACEFUL_SHUTDOWN_SIGNAL = signal.SIGUSR1
"""
The number of the UNIX signal used to communicate graceful shutdown requests
from the main process to the output relay process (an integer). See also
:func:`~MultiProcessHelper.enable_graceful_shutdown()`.
"""

TERMINATION_DELAY = 0.01
"""
The number of seconds to wait before terminating the output relay process (a
floating point number).
"""

PARTIAL_DEFAULT = False
"""Whether partial reads are enabled or disabled by default (a boolean)."""

STDOUT_FD = 1
"""
The number of the file descriptor that refers to the standard output stream (an
integer).
"""

STDERR_FD = 2
"""
The number of the file descriptor that refers to the standard error stream (an
integer).
"""

# Semi-standard module versioning.
__version__ = '2.2'


class MultiProcessHelper(object):

    """
    Helper to spawn and manipulate child processes using :mod:`multiprocessing`.

    This class serves as a base class for :class:`CaptureOutput` and
    :class:`PseudoTerminal` because both classes need the same child process
    handling logic.
    """

    def __init__(self):
        """Construct a :class:`MultiProcessHelper` object."""
        self.processes = []

    def start_child(self, target):
        """
        Start a child process using :class:`multiprocessing.Process`.

        :param target: The callable to run in the child process. Expected to
                       take a single argument which is a
                       :class:`multiprocessing.Event` to be set when the child
                       process has finished initialization.
        """
        started_event = multiprocessing.Event()
        child_process = multiprocessing.Process(target=target, args=(started_event,))
        self.processes.append(child_process)
        child_process.daemon = True
        child_process.start()
        started_event.wait()

    def stop_children(self):
        """
        Gracefully shut down all child processes.

        Child processes are expected to call :func:`enable_graceful_shutdown()`
        during initialization.
        """
        while self.processes:
            child_process = self.processes.pop()
            if child_process.is_alive():
                os.kill(child_process.pid, GRACEFUL_SHUTDOWN_SIGNAL)
            child_process.join()

    def wait_for_children(self):
        """Wait for all child processes to terminate."""
        for child_process in self.processes:
            child_process.join()

    def enable_graceful_shutdown(self):
        """
        Register a signal handler that converts :data:`GRACEFUL_SHUTDOWN_SIGNAL` to an exception.

        Used by :func:`~PseudoTerminal.capture_loop()` to gracefully interrupt
        the blocking :func:`os.read()` call when the capture loop needs to be
        terminated (this is required for coverage collection).
        """
        signal.signal(GRACEFUL_SHUTDOWN_SIGNAL, self.raise_shutdown_request)

    def raise_shutdown_request(self, signum, frame):
        """Raise :exc:`ShutdownRequested` when :data:`GRACEFUL_SHUTDOWN_SIGNAL` is received."""
        raise ShutdownRequested


class CaptureOutput(MultiProcessHelper):

    """Context manager to capture the standard output and error streams."""

    def __init__(self, merged=True, encoding=DEFAULT_TEXT_ENCODING, termination_delay=TERMINATION_DELAY, chunk_size=1024):
        """
        Initialize a :class:`CaptureOutput` object.

        :param merged: Whether to capture and relay the standard output and
                       standard error streams as one stream (a boolean,
                       defaults to :data:`True`). When this is :data:`False`
                       the ``stdout`` and ``stderr`` attributes of the
                       :class:`CaptureOutput` object are
                       :class:`PseudoTerminal` objects that can be used to
                       get at the output captured from each stream separately.
        :param encoding: The name of the character encoding used to decode the
                         captured output (a string, defaults to
                         :data:`DEFAULT_TEXT_ENCODING`).
        :param termination_delay: The number of seconds to wait before
                                  terminating the output relay process (a
                                  floating point number, defaults to
                                  :data:`TERMINATION_DELAY`).
        :param chunk_size: The maximum number of bytes to read from the
                           captured streams on each call to :func:`os.read()`
                           (an integer).
        """
        # Initialize the superclass.
        super(CaptureOutput, self).__init__()
        # Store constructor arguments.
        self.chunk_size = chunk_size
        self.encoding = encoding
        self.merged = merged
        self.termination_delay = termination_delay
        # Initialize instance variables.
        self.pseudo_terminals = []
        self.streams = []
        # Initialize stdout/stderr stream containers.
        self.stdout_stream = self.initialize_stream(sys.stdout, STDOUT_FD)
        self.stderr_stream = self.initialize_stream(sys.stderr, STDERR_FD)

    def initialize_stream(self, file_obj, expected_fd):
        """
        Initialize one or more :class:`Stream` objects to capture a standard stream.

        :param file_obj: A file-like object with a ``fileno()`` method.
        :param expected_fd: The expected file descriptor of the file-like object.
        :returns: The :class:`Stream` connected to the file descriptor of the
                  file-like object.

        By default this method just initializes a :class:`Stream` object
        connected to the given file-like object and its underlying file
        descriptor (a simple one-liner).

        If however the file descriptor of the file-like object doesn't have the
        expected value (``expected_fd``) two :class:`Stream` objects will be
        created instead: One of the stream objects will be connected to the
        file descriptor of the file-like object and the other stream object
        will be connected to the file descriptor that was expected
        (``expected_fd``).

        This approach is intended to make sure that "nested" output capturing
        works as expected: Output from the current Python process is captured
        from the file descriptor of the file-like object while output from
        subprocesses is captured from the file descriptor given by
        ``expected_fd`` (because the operating system defines special semantics
        for the file descriptors with the numbers one and two that we can't
        just ignore).

        For more details refer to `issue 2 on GitHub
        <https://github.com/xolox/python-capturer/issues/2>`_.
        """
        real_fd = file_obj.fileno()
        stream_obj = Stream(real_fd)
        self.streams.append((expected_fd, stream_obj))
        if real_fd != expected_fd:
            self.streams.append((expected_fd, Stream(expected_fd)))
        return stream_obj

    def __enter__(self):
        """Automatically call :func:`start_capture()` when entering a :keyword:`with` block."""
        self.start_capture()
        return self

    def __exit__(self, exc_type=None, exc_value=None, traceback=None):
        """Automatically call :func:`finish_capture()` when leaving a :keyword:`with` block."""
        self.finish_capture()

    @property
    def is_capturing(self):
        """:data:`True` if output is being captured, :data:`False` otherwise."""
        return any(stream.is_redirected for kind, stream in self.streams)

    def start_capture(self):
        """
        Start capturing the standard output and error streams.

        :raises: :exc:`~exceptions.TypeError` when output is already being
                 captured.

        This method is called automatically when using the capture object as a
        context manager. It's provided under a separate name in case someone
        wants to extend :class:`CaptureOutput` and build their own context
        manager on top of it.
        """
        if self.is_capturing:
            raise TypeError("Output capturing is already enabled!")
        if self.merged:
            # Capture and relay stdout/stderr as one stream.
            self.output = self.allocate_pty(relay_fd=self.stderr_stream.original_fd)
            for kind, stream in self.streams:
                self.output.attach(stream)
            # Enable compatibility with the old API where stdout/stderr were
            # unconditionally captured as a single stream and so these methods
            # were available on the CaptureOutput class (because that made
            # sense back then :-).
            for name in ('get_handle', 'get_bytes', 'get_lines', 'get_text', 'save_to_handle', 'save_to_path'):
                setattr(self, name, getattr(self.output, name))
        else:
            # Capture and relay stdout/stderr as separate streams.
            self.output_queue = multiprocessing.Queue()
            self.start_child(self.merge_loop)
            self.stdout = self.allocate_pty(output_queue=self.output_queue, queue_token=STDOUT_FD)
            self.stderr = self.allocate_pty(output_queue=self.output_queue, queue_token=STDERR_FD)
            for kind, stream in self.streams:
                if kind == STDOUT_FD:
                    self.stdout.attach(stream)
                elif kind == STDERR_FD:
                    self.stderr.attach(stream)
                else:
                    raise Exception("Programming error: Unrecognized stream type!")
        # Start capturing and relaying of output (in one or two subprocesses).
        for pseudo_terminal in self.pseudo_terminals:
            pseudo_terminal.start_capture()

    def finish_capture(self):
        """
        Stop capturing the standard output and error streams.

        This method is called automatically when using the capture object as a
        context manager. It's provided under a separate name in case someone
        wants to extend :class:`CaptureOutput` and build their own context
        manager on top of it.
        """
        for pseudo_terminal in self.pseudo_terminals:
            pseudo_terminal.finish_capture()
        self.wait_for_children()

    def allocate_pty(self, relay_fd=None, output_queue=None, queue_token=None):
        """
        Allocate a pseudo terminal.

        Internal shortcut for :func:`start_capture()` to allocate multiple
        pseudo terminals without code duplication.
        """
        obj = PseudoTerminal(
            self.encoding, self.termination_delay, self.chunk_size,
            relay_fd=relay_fd, output_queue=output_queue,
            queue_token=queue_token,
        )
        self.pseudo_terminals.append(obj)
        return obj

    def merge_loop(self, started_event):
        """
        Internal method used to merge and relay output in a child process.

        This method is used when standard output and standard error are being
        captured separately. It's responsible for emitting each captured line
        on the appropriate stream without interleaving text within lines.
        """
        buffers = {
            STDOUT_FD: OutputBuffer(self.stdout_stream.original_fd),
            STDERR_FD: OutputBuffer(self.stderr_stream.original_fd),
        }
        started_event.set()
        while buffers:
            captured_from, output = self.output_queue.get()
            if output:
                buffers[captured_from].add(output)
            else:
                buffers[captured_from].flush()
                buffers.pop(captured_from)


class OutputBuffer(object):

    """
    Helper for :func:`CaptureOutput.merge_loop()` to buffer captured output and
    flush to the appropriate stream after each line break.
    """

    def __init__(self, fd):
        """
        Construct an :class:`OutputBuffer` object.

        :param fd: The number of the file descriptor where output should be
                   flushed (an integer).
        """
        self.fd = fd
        self.buffer = b''

    def add(self, output):
        """
        Add output to the buffer and flush appropriately.

        :param output: The output to add to the buffer (a string).
        """
        self.buffer += output
        if b'\n' in self.buffer:
            before, _, self.buffer = self.buffer.rpartition(b'\n')
            os.write(self.fd, before + b'\n')

    def flush(self):
        """Flush any remaining buffered output to the stream."""
        os.write(self.fd, self.buffer)


class PseudoTerminal(MultiProcessHelper):

    """
    Helper for :class:`CaptureOutput` that manages capturing of output and
    exposing the captured output.
    """

    def __init__(self, encoding, termination_delay, chunk_size, relay_fd, output_queue, queue_token):
        """
        Initialize a :class:`PseudoTerminal` object.

        :param encoding: The name of the character encoding used to decode the
                         captured output (a string, defaults to
                         :data:`DEFAULT_TEXT_ENCODING`).
        :param termination_delay: The number of seconds to wait before
                                  terminating the output relay process (a
                                  floating point number, defaults to
                                  :data:`TERMINATION_DELAY`).
        :param chunk_size: The maximum number of bytes to read from the
                           captured stream(s) on each call to :func:`os.read()`
                           (an integer).
        :param relay_fd: The number of the file descriptor where captured
                         output should be relayed to (an integer or
                         :data:`None` if ``output_queue`` and ``queue_token``
                         are given).
        :param output_queue: The multiprocessing queue where captured output
                             chunks should be written to (a
                             :class:`multiprocessing.Queue` object or
                             :data:`None` if ``relay_fd`` is given).
        :param queue_token: A unique identifier added to each output chunk
                            written to the queue (any value or :data:`None` if
                            ``relay_fd`` is given).
        """
        # Initialize the superclass.
        super(PseudoTerminal, self).__init__()
        # Store constructor arguments.
        self.encoding = encoding
        self.termination_delay = termination_delay
        self.chunk_size = chunk_size
        self.relay_fd = relay_fd
        self.output_queue = output_queue
        self.queue_token = queue_token
        # Initialize instance variables.
        self.streams = []
        # Allocate a pseudo terminal so we can fake subprocesses into
        # thinking that they are connected to a real terminal (this will
        # trigger them to use e.g. ANSI escape sequences).
        self.master_fd, self.slave_fd = pty.openpty()
        # Create a temporary file in which we'll store the output received on
        # the master end of the pseudo terminal.
        self.output_fd, output_file = tempfile.mkstemp()
        self.output_handle = open(output_file, 'rb')
        # Unlink the temporary file because we have a readable file descriptor
        # and a writable file descriptor and that's all we need! If this
        # surprises you I suggest you investigate why unlink() was named the
        # way it was in UNIX :-).
        os.unlink(output_file)

    def attach(self, stream):
        """
        Attach a stream to the pseudo terminal.

        :param stream: A :class:`Stream` object.
        """
        stream.redirect(self.slave_fd)
        self.streams.append(stream)

    def start_capture(self):
        """Start the child process(es) responsible for capturing and relaying output."""
        self.start_child(self.capture_loop)

    def finish_capture(self):
        """Stop the process of capturing output and destroy the pseudo terminal."""
        time.sleep(self.termination_delay)
        self.stop_children()
        self.close_pseudo_terminal()
        self.restore_streams()

    def close_pseudo_terminal(self):
        """Close the pseudo terminal's master/slave file descriptors."""
        for name in ('master_fd', 'slave_fd'):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def restore_streams(self):
        """Restore the stream(s) attached to the pseudo terminal."""
        for stream in self.streams:
            stream.restore()

    def get_handle(self, partial=PARTIAL_DEFAULT):
        """
        Get the captured output as a Python file object.

        :param partial: If :data:`True` (*not the default*) the partial output
                        captured so far is returned, otherwise (*so by
                        default*) the relay process is terminated and output
                        capturing is disabled before returning the captured
                        output (the default is intended to protect unsuspecting
                        users against partial reads).
        :returns: The captured output as a Python file object. The file
                  object's current position is reset to zero before this
                  function returns.

        This method is useful when you're dealing with arbitrary amounts of
        captured data that you don't want to load into memory just so you can
        save it to a file again. In fact, in that case you might want to take a
        look at :func:`save_to_path()` and/or :func:`save_to_handle()` :-).

        .. warning:: Two caveats about the use of this method:

                     1. If partial is :data:`True` (not the default) the output
                        can end in a partial line, possibly in the middle of an
                        ANSI escape sequence or a multi byte character.

                     2. If you close this file handle you just lost your last
                        chance to get at the captured output! (calling this
                        method again will not give you a new file handle)
        """
        if not partial:
            self.finish_capture()
        self.output_handle.seek(0)
        return self.output_handle

    def get_bytes(self, partial=PARTIAL_DEFAULT):
        """
        Get the captured output as binary data.

        :param partial: Refer to :func:`get_handle()` for details.
        :returns: The captured output as a binary string.
        """
        return self.get_handle(partial).read()

    def get_lines(self, interpreted=True, partial=PARTIAL_DEFAULT):
        """
        Get the captured output split into lines.

        :param interpreted: If :data:`True` (the default) captured output is
                            processed using :func:`interpret_carriage_returns()`.
        :param partial: Refer to :func:`get_handle()` for details.
        :returns: The captured output as a list of Unicode strings.

        .. warning:: If partial is :data:`True` (not the default) the output
                     can end in a partial line, possibly in the middle of a
                     multi byte character (this may cause decoding errors).
        """
        output = self.get_bytes(partial)
        output = output.decode(self.encoding)
        if interpreted:
            return interpret_carriage_returns(output)
        else:
            return output.splitlines()

    def get_text(self, interpreted=True, partial=PARTIAL_DEFAULT):
        """
        Get the captured output as a single string.

        :param interpreted: If :data:`True` (the default) captured output is
                            processed using :func:`interpret_carriage_returns()`.
        :param partial: Refer to :func:`get_handle()` for details.
        :returns: The captured output as a Unicode string.

        .. warning:: If partial is :data:`True` (not the default) the output
                     can end in a partial line, possibly in the middle of a
                     multi byte character (this may cause decoding errors).
        """
        output = self.get_bytes(partial)
        output = output.decode(self.encoding)
        if interpreted:
            output = u'\n'.join(interpret_carriage_returns(output))
        return output

    def save_to_handle(self, handle, partial=PARTIAL_DEFAULT):
        """
        Save the captured output to an open file handle.

        :param handle: A writable file-like object.
        :param partial: Refer to :func:`get_handle()` for details.
        """
        shutil.copyfileobj(self.get_handle(partial), handle)

    def save_to_path(self, filename, partial=PARTIAL_DEFAULT):
        """
        Save the captured output to a file.

        :param filename: The pathname of the file where the captured output
                         should be written to (a string).
        :param partial: Refer to :func:`get_handle()` for details.
        """
        with open(filename, 'wb') as handle:
            self.save_to_handle(handle, partial)

    def capture_loop(self, started_event):
        """
        Continuously read from the master end of the pseudo terminal and relay the output.

        This function is run in the background by :func:`start_capture()`
        using the :mod:`multiprocessing` module. It's role is to read output
        emitted on the master end of the pseudo terminal and relay this output
        to the real terminal (so the operator can see what's happening in real
        time) as well as a temporary file (for additional processing by the
        caller).
        """
        self.enable_graceful_shutdown()
        started_event.set()
        try:
            while True:
                # Read from the master end of the pseudo terminal.
                output = os.read(self.master_fd, self.chunk_size)
                if output:
                    # Store the output in the temporary file.
                    os.write(self.output_fd, output)
                    # Relay the output to the real terminal?
                    if self.relay_fd is not None:
                        os.write(self.relay_fd, output)
                    # Relay the output to the master process?
                    if self.output_queue is not None:
                        self.output_queue.put((self.queue_token, output))
                else:
                    # Relinquish our time slice, or in other words: try to be
                    # friendly to other processes when os.read() calls don't
                    # block. Just for the record, all of my experiments have
                    # shown that os.read() on the master file descriptor
                    # returned by pty.openpty() does in fact block.
                    time.sleep(0)
        except ShutdownRequested:
            # Let the master process know that we're shutting down.
            if self.output_queue is not None:
                self.output_queue.put((self.queue_token, ''))


class Stream(object):

    """
    Container for standard stream redirection logic.

    Used by :class:`CaptureOutput` to temporarily redirect the standard output
    and standard error streams.

    .. attribute:: is_redirected

       :data:`True` once :func:`redirect()` has been called, :data:`False` when
       :func:`redirect()` hasn't been called yet or :func:`restore()` has since
       been called.
    """

    def __init__(self, fd):
        """
        Initialize a :class:`Stream` object.

        :param fd: The file descriptor to be redirected (an integer).
        """
        self.fd = fd
        self.original_fd = os.dup(self.fd)
        self.is_redirected = False

    def redirect(self, target_fd):
        """
        Redirect output written to the file descriptor to another file descriptor.

        :param target_fd: The file descriptor that should receive the output
                          written to the file descriptor given to the
                          :class:`Stream` constructor (an integer).
        :raises: :exc:`~exceptions.TypeError` when the file descriptor is
                 already being redirected.
        """
        if self.is_redirected:
            msg = "File descriptor %s is already being redirected!"
            raise TypeError(msg % self.fd)
        os.dup2(target_fd, self.fd)
        self.is_redirected = True

    def restore(self):
        """Stop redirecting output written to the file descriptor."""
        if self.is_redirected:
            os.dup2(self.original_fd, self.fd)
            self.is_redirected = False


def enable_old_api():
    for name in ('get_handle', 'get_bytes', 'get_lines', 'get_text', 'save_to_handle', 'save_to_path'):
        def method_proxy(proxy, *args, **kw):
            if not hasattr(proxy, 'output'):
                raise TypeError("The old calling interface is only supported when merged=True and start_capture() has been called!")
            real_method = getattr(proxy.output, name)
            return real_method(*args, **kw)
        method_proxy.__doc__ = getattr(PseudoTerminal, name).__doc__
        setattr(CaptureOutput, name, method_proxy)


class ShutdownRequested(Exception):

    """
    Raised by :func:`~MultiProcessHelper.raise_shutdown_request()` to signal
    graceful termination requests (in :func:`~PseudoTerminal.capture_loop()`).
    """


enable_old_api()
