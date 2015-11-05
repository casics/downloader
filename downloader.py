#!/usr/bin/env python3.4
#
# @file    convert-db.py
# @brief   Convert database to new record format
# @author  Michael Hucka
#
# <!---------------------------------------------------------------------------
# Copyright (C) 2015 by the California Institute of Technology.
# This software is part of CASICS, the Comprehensive and Automated Software
# Inventory Creation System.  For more information, visit http://casics.org.
# ------------------------------------------------------------------------- -->

import pdb
import sys
import os
import errno
import plac
import wget
import humanize
import zipfile
from time import time

sys.path.append(os.path.join(os.path.dirname(__file__), "../common"))
from dbinterface import *
from utils import *
from reporecord import *


# Globals
# .............................................................................
# List of languages we watch for.

sought_languages = ['Java', 'Python', 'C++']
default_download_dir = "downloads"


# Main body.
# .............................................................................
# Currently this only does GitHub, but extending this to handle other hosts
# should hopefully be possible.

def main(dir=default_download_dir):
    '''Downloads copies of respositories.'''
    download(dir)


def download(dir):
    db = Database()
    dbroot = db.open()
    msg('Downloading ...')
    start = time()
    language_codes = [Language.identifier(lang) for lang in sought_languages]
    entries_examined = 0
    entries_matching = 0
    for key, entry in dbroot.items():
        if not isinstance(entry, RepoEntry):
            continue
        entries_examined += 1

        # Only download repos that have a language we're watching for.
        if not any(lang in entry.languages for lang in language_codes):
            continue
        entries_matching += 1
        # Skip ones we already have a copy of.
        localpath = os.path.join(dir, entry.path)
        if os.path.exists(localpath):
            continue

        # Create a subdirectory if needed and download the zip file.
        print('[{} out of {} examined] {}: '
              .format(entries_matching, entries_examined, entry.path), end='', flush=True)
        os.makedirs(localpath, exist_ok=True)
        url = "https://github.com/{}/archive/master.zip".format(entry.path)
        outfile = wget.download(url, bar=None, out=localpath)
        filesize = file_size(outfile)
        try:
            zipfile.ZipFile(outfile).extractall(localpath)
            os.remove(outfile)
        except Exception as e:
            msg('{} left unzipped: '.format(outfile, str(e)))
            pass
        msg(filesize)
    db.close()
    msg('')
    msg('Done.')


# Helpers
# .............................................................................

def zip_file_exists(path, dir):
    # This is the path that GitHub creates for zip files
    filename = path[path.rfind('/') + 1 :] + '-master.zip'
    return os.path.exists(dir + '/' + path + '/' + filename)


def file_size(path):
    return humanize.naturalsize(os.path.getsize(path))


# Plac annotations for main function arguments
# .............................................................................
# Argument annotations are: (help, kind, abbrev, type, choices, metavar)
# Plac automatically adds a -h argument for help, so no need to do it here.

main.__annotations__ = dict(
    dir = ('download directory root', 'option', 'd', str),
)


# Entry point
# .............................................................................

def cli_main():
    plac.call(main)

cli_main()
