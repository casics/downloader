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
from time import time

from database import *
from reporecord import *
from utils import *


# Globals
# .............................................................................
# List of languages we watch for.

sought_languages = ['Java', 'Python', 'C++']
download_dir = "downloads"


# Main body.
# .............................................................................
# Currently this only does GitHub, but extending this to handle other hosts
# should hopefully be possible.

def main():
    '''Downloads copies of respositories.'''
    download()


def download():
    db = Database()
    dbroot = db.open()
    msg('Downloading ...')
    start = time()
    language_codes = [Language.identifier(lang) for lang in sought_languages]
    for i, key in enumerate(dbroot):
        entry = dbroot[key]
        if not isinstance(entry, RepoEntry):
            continue
        if not any(lang in entry.languages for lang in language_codes):
            continue
        if zip_file_exists(entry.path, download_dir):
            continue

        # Create a subdirectory if needed and download the zip file.
        msg(entry.path + ':')
        dirpath = download_dir + "/" + entry.path
        os.makedirs(dirpath, exist_ok=True)
        url = "https://github.com/{}/archive/master.zip".format(entry.path)
        outpath = dirpath
        wget.download(url, bar=wget.bar_adaptive, out=dirpath)
        msg('')

        if (i + 1) % 100 == 0:
            msg('*** {} entries processed [{:2f}] ***'.format(i + 1, time() - start))
            start = time()
    # update_progress(1)

    db.close()
    msg('')
    msg('Done.')


# Helpers
# .............................................................................

def zip_file_exists(path, dir):
    # This is the path that GitHub creates for zip files
    filename = path[path.rfind('/') + 1 :] + '-master.zip'
    return os.path.exists(dir + '/' + path + '/' + filename)


# Entry point
# .............................................................................

def cli_main():
    plac.call(main)

cli_main()
