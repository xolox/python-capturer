capturer: Easily capture stdout/stderr of the current process and subprocesses
==============================================================================

.. image:: https://travis-ci.org/xolox/python-capturer.svg?branch=master
   :target: https://travis-ci.org/xolox/python-capturer

.. image:: https://coveralls.io/repos/xolox/python-capturer/badge.png?branch=master
   :target: https://coveralls.io/r/xolox/python-capturer?branch=master

The `capturer` package makes it easy to capture the stdout_ and stderr_ streams
of the current process *and subprocesses*. Output can be relayed to the
terminal in real time but is also available to the Python program for
additional processing. It's currently tested on Python 2.6, 2.7, 3.4 and PyPy.
For usage instructions please refer to the documentation_.

.. contents::
   :local:

Status
------

The `capturer` package was developed as a proof of concept over the course of a
weekend, because I was curious to see if it could be done (reliably). After a
weekend of extensive testing it seems to work fairly well so I'm publishing the
initial release as version 1.0, however I still consider this a proof of
concept because I don't have extensive "production" experience using it yet.
Here's hoping it works as well in practice as it did during my testing :-).

Installation
------------

The `capturer` package is available on PyPI_ which means installation should be
as simple as:

.. code-block:: sh

   $ pip install capturer

There's actually a multitude of ways to install Python packages (e.g. the `per
user site-packages directory`_, `virtual environments`_ or just installing
system wide) and I have no intention of getting into that discussion here, so
if this intimidates you then read up on your options before returning to these
instructions ;-).

Getting started
---------------

The easiest way to capture output is to use a context manager:

.. code-block:: python

   import subprocess
   from capturer import CaptureOutput

   with CaptureOutput() as capturer:
       # Generate some output from Python.
       print "Output from Python"
       # Generate output from a subprocess.
       subprocess.call(["echo", "Output from a subprocess"])
       # Get the output in each of the supported formats.
       assert capturer.get_bytes() == b'Output from Python\r\nOutput from a subprocess\r\n'
       assert capturer.get_lines() == [u'Output from Python', u'Output from a subprocess']
       assert capturer.get_text() == u'Output from Python\nOutput from a subprocess'

The use of a context manager (`the with statement`_) ensures that output
capturing is enabled and disabled at the appropriate time, regardless of
whether exceptions interrupt the normal flow of processing.

Note that the first call to `get_bytes()`_, `get_lines()`_ or `get_text()`_
will stop the capturing of output by default. This is intended as a sane
default to prevent partial reads (which can be confusing as hell when you don't
have experience with them). So we could have simply used ``print`` to show
the results without causing a recursive "captured output is printed and then
captured again" loop. There's an optional ``partial=True`` keyword argument
that can be used to disable this behavior (please refer to the documentation_
for details).

Design choices
--------------

There are existing solutions out there to capture the stdout_ and stderr_
streams of (Python) processes. The `capturer` package was created for a very
specific use case that wasn't catered for by existing solutions (that I could
find). This section documents the design choices that guided the development of
the `capturer` package:

.. contents::
  :local:

Intercepts writes to low level file descriptors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Libraries like capture_ and iocapture_ change Python's sys.stdout_ and
sys.stderr_ file objects to fake file objects (using StringIO_). This enables
capturing of (most) output written to the stdout_ and stderr_ streams from the
same Python process, however any output from subprocesses is unaffected by the
redirection and not captured.

The `capturer` package instead intercepts writes to low level file descriptors
(similar to and inspired by `how pytest does it`_). This enables capturing of
output written to the standard output and error streams from the same Python
process as well as any subprocesses.

Uses a pseudo terminal to emulate a real terminal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The `capturer` package uses a pseudo terminal created using `pty.openpty()`_ to
capture output. This means subprocesses will use ANSI escape sequences because
they think they're connected to a terminal. In the current implementation you
can't opt out of this, but feel free to submit a feature request to change this
:-). The use of `pty.openpty()`_ means you need to be running in a UNIX like
environment for `capturer` to work.

Relays output to the terminal in real time
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The main use case of `capturer` is to capture all output of a snippet of Python
code (including any output by subprocesses) but also relay the output to the
terminal in real time. This has a couple of useful properties:

- Long running operations can provide the operator with real time feedback by
  emitting output on the terminal. This sounds obvious (and it is!) but it is
  non-trivial to implement (an understatement :-) when you *also* want to
  capture the output.

- Programs like gpg_ and ssh_ that use interactive password prompts will render
  their password prompt on the terminal in real time. This avoids the awkward
  interaction where a password prompt is silenced but the program still hangs,
  waiting for input on stdin_.

Contact
-------

The latest version of `capturer` is available on PyPI_ and GitHub_. The
documentation is hosted on `Read the Docs`_. For bug reports please create an
issue on GitHub_. If you have questions, suggestions, etc. feel free to send me
an e-mail at `peter@peterodding.com`_.

License
-------

This software is licensed under the `MIT license`_.

© 2015 Peter Odding.

A big thanks goes out to the pytest_ developers because pytest's mechanism for
capturing the output of subprocesses provided inspiration for the `capturer`
package. No code was copied, but both projects are MIT licensed anyway, so it's
not like it's very relevant :-).

.. External references:
.. _capture: https://pypi.python.org/pypi/capture
.. _documentation: https://capturer.readthedocs.org
.. _get_bytes(): https://capturer.readthedocs.org/en/latest/#capturer.CaptureOutput.get_bytes
.. _get_lines(): https://capturer.readthedocs.org/en/latest/#capturer.CaptureOutput.get_lines
.. _get_text(): https://capturer.readthedocs.org/en/latest/#capturer.CaptureOutput.get_text
.. _GitHub: https://github.com/xolox/python-capturer
.. _gpg: https://en.wikipedia.org/wiki/GNU_Privacy_Guard
.. _how pytest does it: https://pytest.org/latest/capture.html
.. _iocapture: https://pypi.python.org/pypi/iocapture
.. _MIT license: http://en.wikipedia.org/wiki/MIT_License
.. _per user site-packages directory: https://www.python.org/dev/peps/pep-0370/
.. _peter@peterodding.com: peter@peterodding.com
.. _pty.openpty(): https://docs.python.org/2/library/pty.html#pty.openpty
.. _PyPI: https://pypi.python.org/pypi/capturer
.. _pytest: https://pypi.python.org/pypi/pytest
.. _Read the Docs: https://capturer.readthedocs.org
.. _ssh: https://en.wikipedia.org/wiki/Secure_Shell
.. _stderr: https://en.wikipedia.org/wiki/Standard_streams#Standard_error_.28stderr.29
.. _stdin: https://en.wikipedia.org/wiki/Standard_streams#Standard_input_.28stdin.29
.. _stdout: https://en.wikipedia.org/wiki/Standard_streams#Standard_output_.28stdout.29
.. _StringIO: https://docs.python.org/2/library/stringio.html
.. _sys.stderr: https://docs.python.org/2/library/sys.html#sys.stderr
.. _sys.stdout: https://docs.python.org/2/library/sys.html#sys.stdout
.. _the with statement: https://docs.python.org/2/reference/compound_stmts.html#the-with-statement
.. _virtual environments: http://docs.python-guide.org/en/latest/dev/virtualenvs/
