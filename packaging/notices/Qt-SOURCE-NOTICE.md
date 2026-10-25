# Qt / PySide6 / Shiboken6

The Windows release dynamically links unmodified Qt for Python 6.8.3 components under GNU LGPL version 3. The LGPL and corresponding GPL text are provided in this directory. Copyright belongs to The Qt Company Ltd and the respective contributors.

Corresponding upstream sources and build instructions:

- Qt 6.8.3: https://download.qt.io/archive/qt/6.8/6.8.3/single/
- PySide/Shiboken 6.8.3: https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=6.8.3
- Qt for Python licensing: https://doc.qt.io/qtforpython-6/licenses.html

The dynamically linked libraries remain replaceable in the installed `_internal` directories. You may modify and replace these LGPL components, and reverse engineer the application for debugging modifications to those components, as permitted by the LGPL. No restriction on these LGPL rights is imposed by this package.

Other dependencies retain their own notices and licenses included here and in the component inventory.
