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
import locale
import http
import urllib
from base64 import b64encode
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
    locale.setlocale(locale.LC_CTYPE, 'en_US.UTF-8')
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

        # Download repos for languages we're watching, skipping ones we
        # already have downloaded.

        if not any(lang in entry.languages for lang in language_codes):
            continue
        localpath = make_path(dir, entry)
        if os.path.exists(localpath):
            continue
        entries_matching += 1

        # Create a subdirectory if needed and download the zip file.

        print('[{} out of {} examined] {}: '
              .format(entries_matching, entries_examined, entry.path), end='', flush=True)
        os.makedirs(localpath, exist_ok=True)
        # Try first with the default master branch.
        url = "https://github.com/{}/archive/master.zip".format(entry.path)
        try:
            outfile = wget.download(url, bar=None, out=localpath)
        except Exception as e:
            # If we get a 404 from GitHub, it may mean there is no "master".
            # To find out what it really is, it costs an API call, so we don't
            # do it unless we really have to.
            if e.code == 404:
                newurl = get_archive_url(entry.path)
                try:
                    outfile = wget.download(newurl, bar=None, out=localpath)
                except Exception as newe:
                    msg('Error attempting to download {}: {}'.format(entry.path, str(newe)))
                    continue
            else:
                msg('Error attempting to download {}: {}'.format(entry.path, str(e)))
                continue

        # Unzip it if we got it.

        filesize = file_size(outfile)
        try:
            zipfile.ZipFile(outfile).extractall(localpath)
            os.remove(outfile)
        except Exception as e:
            msg('{} left zipped: {}'.format(outfile, str(e)))
            continue
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


def get_archive_url(path):
    cfg = Config()
    try:
        login = cfg.get(Host.name(Host.GITHUB), 'login')
        password = cfg.get(Host.name(Host.GITHUB), 'password')
    except Exception as err:
        msg(err)
        text = 'Failed to read "login" and/or "password" for {}'.format(
            Host.name(Host.GITHUB))
        raise SystemExit(text)

    auth = '{0}:{1}'.format(login, password)
    headers = {
        'User-Agent': login,
        'Authorization': 'Basic ' + b64encode(bytes(auth, 'ascii')).decode('ascii'),
        'Accept': 'application/vnd.github.v3.raw',
    }
    url = "https://api.github.com/repos/{}/zipball".format(path)

    conn = http.client.HTTPSConnection("api.github.com")
    conn.request("GET", url, {}, headers)
    response = conn.getresponse()
    if response.status == 302:
        try:
            return next(y for x, y in response.getheaders() if x == 'Location')
        except:
            return None
    elif response.status == 200:
        return response.readall().decode('utf-8')
    else:
        return None


def make_path(dir, entry):
    '''Creates a path of the following form:
        dir/a/b/owner/path
    where
        dir   = the first argument
        a     = the first character of the owner's name
        b     = the second character of the owner's name
        owner = the owner's name
        path  = the path (really, the full name) of the repository on GitHub

    If the owner's name has only 1 character, then the path is:
        dir/a/@/path
    where "@" is the literal character "@".  Since there can only be one
    repository whose names have single characters (0, 1, 2, ..., a, b, c, ...)
    then there can only be one 0/@, 1/@, 2/@, ..., a/@, b/@, etc., so the
    contents of these directories are the repositories for that owner.
    '''
    subpath = entry.path[entry.path.find('/') + 1:]
    first = entry.owner[0]
    if len(entry.owner) == 1:           # Single-character owner name
        return os.path.join(dir, first, '@', subpath)
    else:                               # Multicharacter owner name
        second = entry.owner[1]
        return os.path.join(dir, first, second, entry.owner, subpath)


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
