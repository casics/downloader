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
import shutil
from base64 import b64encode
from time import time

sys.path.append(os.path.join(os.path.dirname(__file__), "../common"))
from dbinterface import *
from utils import *
from reporecord import *


# Globals
# .............................................................................

default_download_dir = "downloads"
max_failures = 10


# Main body.
# .............................................................................
# Currently this only does GitHub, but extending this to handle other hosts
# should hopefully be possible.

def main(downloads_root=default_download_dir, file=None, id=None):
    '''Downloads copies of respositories.'''
    if id:
        id_list = [int(id)]
    elif file:
        with open(file) as f:
            id_list = [int(x) for x in f.read().splitlines()]
    else:
        msg('Need to provide a list of what to download')
        return
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    download(downloads_root, id_list)


def download(downloads_root, id_list):
    dbinterface = Database()
    db = dbinterface.open()

    repo_iterator = iter(id_list)
    total = len(id_list)

    msg('Downloading ...')
    count = 0
    failures = 0
    start = time()
    while failures < max_failures:
        try:
            key = next(repo_iterator)
            if key not in db:
                msg('Unknown identifier #{}'.format(key))
                continue

            entry = db[key]

            localpath = generate_path(downloads_root, entry)
            if os.path.exists(localpath) and os.listdir(localpath):
                # Skip it if we already have it.
                msg('Already have #{} ({}/{})'.format(key, entry.owner, entry.name))
                continue
            count += 1

            # Try first with the default master branch.
            try:
                url = "https://github.com/{}/{}/archive/master.zip".format(
                    entry.owner, entry.name)
                outfile = wget.download(url, bar=None, out=downloads_root)
            except Exception as e:
                # If we get a 404 from GitHub, it may mean there is no "master".
                # To find out what it really is, it costs an API call, so we don't
                # do it unless we really have to.
                if e.code == 404:
                    newurl = get_archive_url(entry)
                    try:
                        outfile = wget.download(newurl, bar=None, out=downloads_root)
                    except Exception as newe:
                        msg('Failed to download {}: {}'.format(entry.id, str(newe)))
                        failures += 1
                        continue
                else:
                    msg('Failed to download {}: {}'.format(entry.id, str(e)))
                    failures += 1
                    continue

            # Unzip it to a temporary path, then move it to the final location.

            filesize = file_size(outfile)
            try:
                zipfile.ZipFile(outfile).extractall(downloads_root)
                os.remove(outfile)
            except Exception as e:
                msg('{} left zipped: {}'.format(outfile, str(e)))
                failures += 1
                continue

            os.makedirs(os.path.dirname(localpath), exist_ok=True)
            zipdir = outfile[0:outfile.rfind('.zip')]
            os.rename(zipdir, localpath)

            failures = 0
            msg('{}/{} (#{} in {}, zip size {})'.format(
                entry.owner, entry.name, key, localpath, filesize))
            if count % 100 == 0:
                msg('{} [{:2f}]'.format(count, time() - start))
                start = time()
        except StopIteration:
            break
        except Exception as err:
            msg('Exception: {0}'.format(err))
            continue

    dbinterface.close()
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


def get_archive_url(entry):
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
    url = "https://api.github.com/repos/{}/{}/zipball".format(entry.owner, entry.name)

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


def generate_path(root, entry):
    '''Creates a path of the following form:
        nn/nn/nn/nn
    where n is an integer 0..9.  For example,
        00/00/00/01
        00/00/00/62
        00/15/63/99
    The full number read left to right (without the slashes) is the identifier
    of the repository (which is the same as the database key in our database).
    The numbers are zero-padded.  So for example, repository entry #7182480
    leads to a path of "07/18/24/80".
    '''
    s = '{:08}'.format(entry.id)
    return os.path.join(root, s[0:2], s[2:4], s[4:6], s[6:8])


# Plac annotations for main function arguments
# .............................................................................
# Argument annotations are: (help, kind, abbrev, type, choices, metavar)
# Plac automatically adds a -h argument for help, so no need to do it here.

main.__annotations__ = dict(
    downloads_root = ('download directory root',                'option', 'd', str),
    file           = ('file containing repository identifiers', 'option', 'f'),
    id             = ('(single) repository identifier',         'option', 'i'),
)


# Entry point
# .............................................................................

def cli_main():
    plac.call(main)

cli_main()
