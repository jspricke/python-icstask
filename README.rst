Python library to convert between Taskwarrior and vObject
=========================================================

* Reads and writes `Taskwarrior <https://taskwarrior.org/>`_ Data.

Installation
------------

You need to have the `taskchampion-py <https://github.com/GothenburgBitFactory/taskchampion-py>`_ library installed.
For Debian/Ubuntu use::

  $ sudo apt-get install python3-taskchampion-py

Using pip
~~~~~~~~~

::

  $ pip install icstask

This will install all Python dependencies as well.

Using python-setuptools
~~~~~~~~~~~~~~~~~~~~~~~

::

  $ python3 setup.py install

Known limitations
-----------------

iCalendar -> Taskwarrior
~~~~~~~~~~~~~~~~~~~~~~~~

* PERCENT-COMPLETE is not supported as there is no representation in Taskwarrior.

VEVENT entries
~~~~~~~~~~~~~~

This project only handles VTODO entries. If you wan to import VEVENT entries try something like:

::

  $ sed -e 's/VEVENT/VTODO/' -e 's/DTSTART/DUE/' ww.ics | ics2task
