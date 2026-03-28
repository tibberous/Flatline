"""
setup.py — Flatline Python Process Debugger
Legacy setuptools installer. For modern installs, use pyproject.toml.
"""
from setuptools import setup, find_packages

setup(
    name='flatline-debugger',
    version='1.0b0',
    description='Python process supervisor and crash debugger with live heartbeat monitoring',
    long_description=open('README.md', encoding='utf-8').read(),
    long_description_content_type='text/markdown',
    author='Trent Tompkins',
    author_email='trenttompkins@gmail.com',
    url='https://flatline.triodesktop.com/',
    project_urls={
        'Source':   'https://github.com/tibberous/Flatline',
        'Tracker':  'https://github.com/tibberous/Flatline/issues',
        'Homepage': 'https://flatline.triodesktop.com/',
    },
    license='MIT',
    py_modules=['flatline'],
    packages=find_packages(where='source'),
    package_dir={'': 'source'},
    python_requires='>=3.10',
    install_requires=[],         # zero required deps — pure stdlib
    extras_require={
        'app':      ['PySide6>=6.4.0'],
        'database': ['pymysql>=1.0.0'],
        'all':      ['PySide6>=6.4.0', 'pymysql>=1.0.0'],
    },
    entry_points={
        'console_scripts': [
            'flatline=flatline:main',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Topic :: Software Development :: Debuggers',
        'Topic :: System :: Monitoring',
        'Environment :: Console',
    ],
    keywords='debugger supervisor crash monitor heartbeat flatline',
)
