# Easily capture stdout/stderr of the current process and subprocesses.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 14, 2015
# URL: https://capturer.readthedocs.org

# Standard library modules.
import random
import string
import subprocess
import sys
import time
import unittest

# The module we're testing.
from capturer import interpret_carriage_returns, CaptureOutput, Stream


class CapturerTestCase(unittest.TestCase):

    def test_carriage_return_interpretation(self):
        # Simple output should pass through unharmed.
        assert interpret_carriage_returns('foo') == ['foo']
        # Simple output should pass through unharmed.
        assert interpret_carriage_returns('foo\nbar') == ['foo', 'bar']
        # Carriage returns and preceding substrings should be stripped.
        assert interpret_carriage_returns('foo\rbar\nbaz') == ['bar', 'baz']
        # Trailing empty lines should be stripped.
        assert interpret_carriage_returns('foo\nbar\nbaz\n\n\n') == ['foo', 'bar', 'baz']

    def test_error_handling(self):
        capturer = CaptureOutput()
        # Nested CaptureOutput.prepare_capture() calls should raise an exception.
        capturer.prepare_capture()
        try:
            self.assertRaises(TypeError, capturer.prepare_capture)
        finally:
            capturer.finish_capture()
        # Nested Stream.redirect() calls should raise an exception.
        stream = Stream(sys.stdout.fileno())
        stream.redirect(sys.stderr.fileno())
        self.assertRaises(TypeError, stream.redirect, sys.stderr.fileno())

    def test_stdout_capture_same_process(self):
        expected_stdout = random_string()
        with CaptureOutput() as capturer:
            print(expected_stdout)
            assert expected_stdout in capturer.get_lines()

    def test_stderr_capture_same_process(self):
        expected_stderr = random_string()
        with CaptureOutput() as capturer:
            sys.stderr.write(expected_stderr + "\n")
            assert expected_stderr in capturer.get_lines()

    def test_combined_capture_same_process(self):
        expected_stdout = random_string()
        expected_stderr = random_string()
        with CaptureOutput() as capturer:
            sys.stdout.write(expected_stdout + "\n")
            sys.stderr.write(expected_stderr + "\n")
            assert expected_stdout in capturer.get_lines()
            assert expected_stderr in capturer.get_lines()

    def test_stdout_capture_subprocess(self):
        expected_stdout = random_string()
        with CaptureOutput() as capturer:
            subprocess.call([
                sys.executable,
                '-c',
                'import sys; sys.stdout.write(%r)' % (expected_stdout + '\n'),
            ])
            assert expected_stdout in capturer.get_lines()

    def test_stderr_capture_subprocess(self):
        expected_stderr = random_string()
        with CaptureOutput() as capturer:
            subprocess.call([
                sys.executable,
                '-c',
                'import sys; sys.stderr.write(%r)' % (expected_stderr + '\n'),
            ])
            assert expected_stderr in capturer.get_lines()

    def test_combined_capture_subprocess(self):
        expected_stdout = random_string()
        expected_stderr = random_string()
        with CaptureOutput() as capturer:
            subprocess.call([
                sys.executable,
                '-c',
                'import sys;' \
                + 'sys.stdout.write(%r);' % (expected_stdout + '\n') \
                + 'sys.stderr.write(%r);' % (expected_stderr + '\n')
            ])
            assert expected_stdout in capturer.get_lines()
            assert expected_stderr in capturer.get_lines()

    def test_combined_current_and_subprocess(self):
        # Some unique strings that are not substrings of each other.
        cur_stdout_1 = "Some output from Python's print statement"
        cur_stdout_2 = "Output from Python's sys.stdout.write() method"
        cur_stdout_3 = "More output from Python's print statement"
        cur_stderr = "Output from Python's sys.stderr.write() method"
        sub_stderr = "Output from subprocess stderr stream"
        sub_stdout = "Output from subprocess stdout stream"
        with CaptureOutput() as capturer:
            # Emit multiple lines on both streams from current process and subprocess.
            print(cur_stdout_1)
            sys.stderr.write("%s\n" % cur_stderr)
            subprocess.call(["sh", "-c", "echo %s 1>&2" % sub_stderr])
            subprocess.call(["echo", sub_stdout])
            sys.stdout.write("%s\n" % cur_stdout_2)
            print(cur_stdout_3)
            # Verify that all of the expected lines were captured.
            assert all(l in capturer.get_lines() for l in (
                cur_stdout_1, cur_stderr, sub_stderr,
                sub_stdout, cur_stdout_2, cur_stdout_3,
            ))

    def test_partial_read(self):
        # This test method uses retry logic because `partial=True' makes these
        # tests prone to race conditions (this is the whole reason why
        # `partial=False' by default :-).
        initial_part = random_string()
        later_part = random_string()
        with CaptureOutput() as capturer:
            sys.stderr.write("%s\n" % initial_part)
            retry(lambda: initial_part in capturer.get_lines(partial=True))
            sys.stderr.write("%s\n" % later_part)
            retry(lambda: later_part in capturer.get_lines(partial=True))

    def test_non_interpreted_lines_capture(self):
        expected_output = random_string()
        with CaptureOutput() as capturer:
            sys.stderr.write("%s\n" % expected_output)
            assert expected_output in capturer.get_lines(interpreted=False)

    def test_text_capture(self):
        expected_output = random_string()
        with CaptureOutput() as capturer:
            sys.stderr.write("%s\n" % expected_output)
            assert expected_output in capturer.get_text()


def random_string():
    length = random.randint(25, 100)
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for i in range(length))


def retry(func, timeout=10):
    time_started = time.time()
    while (time.time() - time_started) < timeout:
        if func():
            return
    assert False, "Timeout expired but function never passed all assertions!"
