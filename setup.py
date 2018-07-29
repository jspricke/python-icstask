from setuptools import setup

setup(name='icstask',
      version='0.1.7',
      description='Python library to convert between Taskwarrior and vObject',
      long_description=open('README.rst').read(),
      author='Jochen Sprickerhof',
      author_email='taskwarrior@jochen.sprickerhof.de',
      license='GPLv3+',
      url='https://github.com/jspricke/python-icstask',
      keywords=['Taskwarrior'],
      classifiers=[
          'Programming Language :: Python',
          'Development Status :: 4 - Beta',
          'Environment :: Console',
          'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)',
          'Topic :: Software Development :: Libraries :: Python Modules',
      ],

      install_requires=['tzlocal', 'vobject'],
      py_modules=['icstask'],

      entry_points={
          'console_scripts': [
              'task2ics = icstask:task2ics',
              'ics2task = icstask:ics2task',
          ]
      },)
