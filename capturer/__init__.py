# Easily capture stdout/stderr of the current process and subprocesses.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 14, 2015
# URL: https://capturer.readthedocs.org

# Standard library modules.
import multiprocessing
import os
import pty
import signal
import sys
import tempfile
import time

DEFAULT_TEXT_ENCODING = 'UTF-8'
"""
The name of the default character encoding used to convert captured output to
Unicode text (a string).
"""

GRACEFUL_SHUTDOWN_SIGNAL = signal.SIGUSR1
"""
The number of the UNIX signal used to communicate graceful shutdown requests
from the main process to the output relay process (an integer). See also
:func:`~CaptureOutput.respect_shutdown_requests()`.
"""

TERMINATION_DELAY = 0.01
"""
The number of seconds to wait before terminating the output relay process (a
floating point number).
"""

# Semi-standard module versioning.
__version__ = '1.0'


class CaptureOutput(object):

    """
    Context manager to capture the standard output and error streams.
    """

    def __init__(self, encoding=DEFAULT_TEXT_ENCODING, termination_delay=TERMINATION_DELAY, chunk_size=1024):
        """
        Initialize a :class:`CaptureOutput` object.

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
        self.cached_output = b''
        self.chunk_size = chunk_size
        self.encoding = encoding
        self.master_fd = None
        self.output_fd = None
        self.output_file = None
        self.relay_process = None
        self.slave_fd = None
        self.stderr = Stream(sys.stderr.fileno())
        self.stdout = Stream(sys.stdout.fileno())
        self.termination_delay = termination_delay

    def __enter__(self):
        self.prepare_capture()
        self.start_relaying()
        return self

    def __exit__(self, exc_type=None, exc_value=None, traceback=None):
        self.stop_relaying()
        self.finish_capture()

    @property
    def is_capturing(self):
        """:data:`True` if output is being captured, :data:`False` otherwise."""
        return self.stdout.is_redirected or self.stderr.is_redirected

    def prepare_capture(self):
        """
        Prepare to capture the standard output and error streams.

        :raises: :exc:`~exceptions.TypeError` when output is already being
                 captured.

        This method is called automatically when using the capture object as a
        context manager. It's provided under a separate name in case someone
        wants to extend :class:`CaptureOutput` and build their own context
        manager on top of it.

        .. note:: If you're calling this method manually please note that no
                  output is captured until you call :func:`start_relaying()`
                  because the subprocess started by that method is responsible
                  for reading captured output.
        """
        if self.is_capturing:
            raise TypeError("Output capturing is already enabled!")
        # Allocate a pseudo terminal so we can fake subprocesses into
        # thinking that they are connected to a real terminal (this will
        # trigger them to use e.g. ANSI escape sequences).
        self.master_fd, self.slave_fd = pty.openpty()
        # Redirect the standard output and error streams to the slave end of
        # the pseudo terminal (subprocesses spawned after this point will
        # automatically inherit the redirection).
        for stream in (self.stdout, self.stderr):
            stream.redirect(self.slave_fd)
        # Create a temporary file in which we'll store the output received on
        # the master end of the pseudo terminal.
        self.output_fd, self.output_file = tempfile.mkstemp()

    def finish_capture(self):
        """
        Stop capturing the standard output and error streams.

        This method is called automatically when using the capture object as a
        context manager. It's provided under a separate name in case someone
        wants to extend :class:`CaptureOutput` and build their own context
        manager on top of it.
        """
        # Close the file descriptors of the pseudo terminal?
        for name in ('master_fd', 'slave_fd'):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)
        # Restore the original stdout/stderr streams?
        for stream in (self.stdout, self.stderr):
            stream.restore()
        # Process the temporary file?
        if self.output_file:
            # Read the captured output into memory.
            self.cache_output()
            # Remove the temporary file.
            os.unlink(self.output_file)
            self.output_file = None

    def start_relaying(self):
        """Start the child process that relays captured output to the terminal in real time."""
        self.started_event = multiprocessing.Event()
        self.relay_process = multiprocessing.Process(target=self.relay_loop)
        self.relay_process.daemon = True
        self.relay_process.start()
        self.started_event.wait()

    def stop_relaying(self):
        """Stop the child process started by :func:`start_relaying()`."""
        if self.relay_process and self.relay_process.is_alive():
            # FIXME I really don't like `voodoo' but without the following
            #       sleep() call some tests will fail sporadically.
            #
            #       If nothing else I'd like to at least be able to explain why
            #       this is needed, but I don't know! I've tried all flush()
            #       and close() permutations I could think of, I tried the
            #       fcntl and select modules, etc. but only sleep() works
            #       reliably?!
            time.sleep(self.termination_delay)
            os.kill(self.relay_process.pid, GRACEFUL_SHUTDOWN_SIGNAL)
            self.relay_process.join()
            self.relay_process = None

    def cache_output(self):
        """
        Read the output captured in the temporary file and store it in memory.

        Used by :func:`finish_capture()` and :func:`get_bytes()` to (re)read
        the temporary file.
        """
        if self.output_file and os.path.isfile(self.output_file):
            with open(self.output_file, 'rb') as handle:
                self.cached_output = handle.read()

    def get_bytes(self, partial=False):
        """
        Get the captured output as binary data.

        :param partial: If :data:`True` (not the default) the partial output
                        captured so far is returned, otherwise (so by default)
                        the relay process is terminated and output capturing is
                        disabled before returning the captured output.
        :returns: The captured output as a binary string.

        .. warning:: If partial is :data:`True` (not the default) the output
                     can end in a partial line, possibly in the middle of an
                     ANSI escape sequence or a multi byte character.
        """
        if partial:
            self.cache_output()
        else:
            self.stop_relaying()
            self.finish_capture()
        return self.cached_output

    def get_lines(self, interpreted=True, partial=False):
        """
        Get the captured output split into lines.

        :param interpreted: If :data:`True` (the default) captured output is
                            processed using :func:`interpret_carriage_returns()`.
        :param partial: If :data:`True` (not the default) the partial output
                        captured so far is returned, otherwise (so by default)
                        the relay process is terminated and output capturing is
                        disabled before returning the captured output.
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

    def get_text(self, interpreted=True, partial=False):
        """
        Get the captured output as a single string.

        :param interpreted: If :data:`True` (the default) captured output is
                            processed using :func:`interpret_carriage_returns()`.
        :param partial: If :data:`True` (not the default) the partial output
                        captured so far is returned, otherwise (so by default)
                        the relay process is terminated and output capturing is
                        disabled before returning the captured output.
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

    def relay_loop(self):
        """
        Continuously read from the master end of the pseudo terminal and relay the output.

        This function is run in the background by :func:`start_relaying()`
        using the :mod:`multiprocessing` module. It's role is to read output
        emitted on the master end of the pseudo terminal and relay this output
        to the real terminal (so the operator can see what's happening in real
        time) as well as a temporary file (for additional processing by the
        caller).
        """
        self.respect_shutdown_requests()
        self.started_event.set()
        try:
            while True:
                # Read from the master end of the pseudo terminal.
                output = os.read(self.master_fd, self.chunk_size)
                if output:
                    # Store the output in the temporary file.
                    os.write(self.output_fd, output)
                    # Relay the output to the real terminal.
                    os.write(self.stderr.original_fd, output)
                else:
                    # Relinquish our time slice, or in other words: try to be
                    # friendly to other processes when os.read() calls don't
                    # block. Just for the record, all of my experiments have
                    # shown that os.read() on the master file descriptor
                    # returned by pty.openpty() does in fact block.
                    time.sleep(0)
        except ShutdownRequested:
            pass

    def respect_shutdown_requests(self):
        """
        Register a signal handler that converts :data:`GRACEFUL_SHUTDOWN_SIGNAL` to an exception.

        Used by :func:`relay_loop()` to gracefully interrupt the blocking
        :func:`os.read()` call when the relay loop needs to be terminated (this
        is required for coverage collection).
        """
        signal.signal(GRACEFUL_SHUTDOWN_SIGNAL, self.raise_shutdown_request)

    def raise_shutdown_request(self, signum, frame):
        """Raises :exc:`ShutdownRequested` when :data:`GRACEFUL_SHUTDOWN_SIGNAL` is received."""
        raise ShutdownRequested


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
        """
        Stop redirecting output written to the file descriptor.
        """
        if self.is_redirected:
            os.dup2(self.original_fd, self.fd)
            self.is_redirected = False


def interpret_carriage_returns(text):
    """
    Emulate the effect of carriage returns on terminals.

    :param text: The raw text containing carriage returns and line feeds (a
                 Unicode string).
    :returns: A list of Unicode strings (one for each line).

    This function works as follows:

    1. The given string is split on line feed characters (represented as
       ``\\n`` in Python).
    2. Any leading and trailing carriage return characters (represented as
       ``\\r`` in Python) are stripped from each line.
    3. The remaining text in each line is split on carriage return characters
       and the last carriage return delimited substring is used as the line.
    4. Trailing empty lines are stripped.

    .. note:: Formally speaking the effect of carriage returns cannot be
              emulated outside of an actual terminal (due to the interaction
              between overlapping output, terminal widths and line wrapping).
              The goal of this function is to sanitize noise in terminal output
              while preserving useful output. Think of it as a useful and
              pragmatic but lossy conversion.
    """
    result = []
    for line in text.split('\n'):
        # Strip leading and/or trailing carriage returns.
        line = line.strip('\r')
        # Split the line on any remaining carriage returns.
        parts = line.split('\r')
        # Preserve only the last carriage return delimited substring.
        result.append(parts[-1])
    # Remove empty trailing lines.
    while result and not result[-1]:
        result.pop(-1)
    return result


class ShutdownRequested(Exception):
    """Raised by :func:`~CaptureOutput.raise_shutdown_request()` to signal graceful termination requests (inside :func:`~CaptureOutput.relay_loop()`)."""
