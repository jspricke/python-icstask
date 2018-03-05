Python library to convert between Taskwarrior and vObject
=========================================================

* Reads and writes `Taskwarrior <https://taskwarrior.org/>`_ Data.

Installation
------------

You need to have the Taskwarrior command line tool installed.
For Debian/Ubuntu use::

  $ sudo apt-get install task

Using pip
~~~~~~~~~

::

  $ pip install icstask

This will install all Python dependencies as well.

Using python-setuptools
~~~~~~~~~~~~~~~~~~~~~~~

::

  $ python setup.py install

Known limitations
-----------------

iCalendar -> Taskwarrior
~~~~~~~~~~~~~~~~~~~~~~~~

* PERCENT-COMPLETE is not supported as there is no representation in Taskwarrior.

VEVENT entries
~~~~~~~~~~~~~~

This project only handles VEVENT entries. If you wan to import VEVENT entries try something like:

::

  $ sed -e 's/VEVENT/VTODO/' -e 's/DTSTART/DUE/' ww.ics | ics2task
