#!/usr/bin/env python3
### BEGIN LICENSE
# Copyright (C) 2013 Peter Levi <peterlevi@peterlevi.com>
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranties of
# MERCHANTABILITY, SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR
# PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.
### END LICENSE

from setuptools import setup, find_packages

setup(
    name='ojo',
    version='0.3',
    license='GPL-3.0',
    author='Peter Levi',
    author_email='peterlevi@peterlevi.com',
    description='Ojoooo Image Viewer',
    long_description='A fast and good-looking image viewer with RAW support, '
                     'nice as a preliminary stage in a photography workflow',
    url='https://github.com/kamenlevi/ojo',
    packages=find_packages(),
    package_data={
        'ojo': [],
    },
    data_files=[
        ('share/applications', ['ojo.desktop.in']),
    ],
    scripts=['bin/ojo'],
    python_requires='>=3.8',
    install_requires=[
        'Pillow',
        'PyGObject',
    ],
)
