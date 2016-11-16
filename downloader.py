#!/usr/bin/env python3.4
#
# @file    download.py
# @brief   Download source code for repos
# @author  Michael Hucka
#
# <!---------------------------------------------------------------------------
# Copyright (C) 2015 by the California Institute of Technology.
# This software is part of CASICS, the Comprehensive and Automated Software
# Inventory Creation System.  For more information, visit http://casics.org.
# ------------------------------------------------------------------------- -->

import sys
import os
import errno
import plac
import wget
import github3
import humanize
import zipfile
import magic
import locale
import http
import urllib
import requests
import shutil
import datetime
from base64 import b64encode
from time import time, sleep

sys.path.append('../database')
sys.path.append('../comment')

from casicsdb import *
from utils import *
from github import *


# Globals
# .............................................................................

default_download_dir = "downloads"
max_failures = 10


# Main body.
# .............................................................................
# Currently this only does GitHub, but extending this to handle other hosts
# should hopefully be possible.

def main(downloads_root=default_download_dir, file=None, id=None, username=None):
    '''Downloads copies of respositories.'''
    if id:
        id_list = [int(x) for x in id.split(',')]
    elif file:
        with open(file) as f:
            id_list = [int(x) for x in f.read().splitlines()]
    else:
        msg('Need to provide identifiers of repositories to be downloaded')
        return
    (user, password) = GitHub.login('github', username)
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    get_sources(downloads_root, id_list, user, password)


def get_sources(downloads_root, id_list, user, password):
    casicsdb  = CasicsDB()
    github_db = casicsdb.open('github')
    repos     = github_db.repos

    downloads_tmp = os.path.join(downloads_root, 'tmp')
    os.makedirs(downloads_tmp, exist_ok=True)

    msg('Downloading {} repos to {}'.format(len(id_list), downloads_root))
    msg('Using temporary directory in {}'.format(downloads_tmp))

    fields = dict.fromkeys(['owner', 'name', 'default_branch'], 1)

    count = 0
    failures = 0
    retry_after_max_failures = True
    start = time()
    for id in iter(id_list):
        retry = True
        while retry and failures < max_failures:
            # Don't retry unless the problem may be transient.
            retry = False
            entry = repos.find_one({'_id': id}, fields)
            if not entry:
                msg('*** skipping unknown GitHub id {}'.format(id))
            if download(entry, downloads_tmp, downloads_root, user, password):
                failures = 0
            else:
                failures += 1

        if failures >= max_failures:
            # Pause & continue once, in case of transient network issues.
            if retry_after_max_failures:
                msg('*** Pausing because of too many consecutive failures')
                sleep(120)
                failures = 0
                retry_after_max_failures = False
            else:
                # We've already paused & restarted once.
                msg('*** Stopping because of too many consecutive failures')
                break
        count += 1
        if count % 100 == 0:
            msg('{} [{:2f}]'.format(count, time() - start))
            start = time()
    msg('')
    msg('Done.')


def download(entry, downloads_tmp, downloads_root, user, password):
    localpath = generate_path(downloads_root, entry)
    if os.path.exists(localpath) and os.listdir(localpath):
        # Skip it if we already have it.
        msg('already have {} -- skipping'.format(e_summary(entry)))
        return True

    # Try first with the default master branch.
    outfile = None
    try:
        url = "https://github.com/{}/{}/archive/{}.zip".format(
            entry['owner'], entry['name'], entry['default_branch'])
        outfile = wget.download(url, bar=None, out=downloads_tmp)
    except Exception as e:
        # If we get a 404 from GitHub, it may mean there is no zip file for
        # what we think is the default branch.  To find out what it really
        # is, we first try scraping the web page, and if that fails, we
        # resort to using an API call.
        if hasattr(e, 'code') and e.code == 404:
            msg('no zip file for branch {} of {} -- looking at alternatives'
                .format(entry['default_branch'], e_summary(entry)))
            newurl = get_archive_url_by_scraping(entry)
            if newurl:
                try:
                    outfile = wget.download(newurl, bar=None, out=downloads_tmp)
                except Exception as newe:
                    msg('*** failed to download {}: {}'.format(entry['_id'], str(newe)))
                    return False
            else:
                newurl = get_archive_url_by_api(entry, user, password)
                if newurl:
                    try:
                        outfile = wget.download(newurl, bar=None, out=downloads_tmp)
                    except Exception as newe:
                        msg('*** failed to download {}: {}'.format(entry['_id'], str(newe)))
                        return False
                else:
                    msg("*** can't find download URL for {}, branch '{}'".format(
                        e_summary(entry), entry['default_branch']))
                    return False
        else:
            msg('*** failed to download {}: {}'.format(entry['_id'], str(e)))
            return False

    # Unzip it to a temporary path, then move it to the final location.

    filesize = file_size(outfile)
    try:
        outdir = unzip_archive(outfile, downloads_tmp)
        os.remove(outfile)
    except Exception as e:
        msg('{} left zipped: {}'.format(outfile, str(e)))
        return False

    os.makedirs(os.path.dirname(localpath), exist_ok=True)
    os.rename(outdir, localpath)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg('{} --> {}, zip size {}, finished at {}'.format(
        e_summary(entry), localpath, filesize, now))
    return True


# Helpers
# .............................................................................

def unzip_archive(file, dest):
    # This only unzips what it guesses to be text files; for binary files,
    # it only creates the file name but leaves out the content.  (This is
    # our approach to saving disk space, because our analysis approach needs
    # text only.)  It returns the path to the directory where of the contents.

    zf = zipfile.ZipFile(file, 'r')
    infolist = zf.infolist()
    # The first item is the name of the root directory.
    if infolist:
        outdir = os.path.join(os.path.dirname(file), infolist[0].filename)
    for component in infolist:
        # zip files often cause problems on Linux because Linux file/dir
        # names don't allow many character encodings.  I thought that setting
        # the locale (see earlier in this file) would take care of that, but
        # it didn't.  That's the reason for the encode/decode wonkiness here.
        name = component.filename.encode('utf-8', 'ignore').decode('ascii', 'ignore')
        dir_name = os.path.join(dest, os.path.dirname(name))
        full_path = os.path.join(dir_name, os.path.basename(name))

        # Create missing directories.
        os.makedirs(dir_name, exist_ok=True)

        # Write files.
        try:
            if not name.endswith('/'):
                content = zf.read(component)
                if probably_text(name, content):
                    with open(full_path, 'wb') as fd:
                        fd.write(content)
                else:
                    os.mknod(full_path)
        except:
            continue

    zf.close()
    return outdir


def probably_text(filename, content):
    if not content:
        # Empty files are considered text.
        return True
    return (magic.from_buffer(content).find('text') >= 0)


def file_size(path):
    return humanize.naturalsize(os.path.getsize(path))


def get_home_page_text(entry):
    url = 'http://github.com/' + entry['owner'] + '/' + entry['name']
    r = requests.get(url)
    return r.text if r.status_code == 200 else None


def get_archive_url_by_scraping(entry):
    html = get_home_page_text(entry)
    if not html:
        return None
    regionstart = html.find('<div class="file-navigation-option">')
    if regionstart < 1:
        return None
    regionend = html.find('Download ZIP', regionstart)
    pathstart = html.find('<a href="', regionstart, regionend)
    if not pathstart:
        return None
    pathstartlen = len('<a href="')
    pathend = html.find('"', pathstart + pathstartlen)
    return 'http://github.com/' + html[pathstart + pathstartlen + 1 : pathend]


def get_archive_url_by_api(entry, user, password):
    auth = '{0}:{1}'.format(user, password)
    headers = {
        'User-Agent': user,
        'Authorization': 'Basic ' + b64encode(bytes(auth, 'ascii')).decode('ascii'),
        'Accept': 'application/vnd.github.v3.raw',
    }
    url = "https://api.github.com/repos/{}/{}/zipball".format(
        entry['owner'], entry['name'])

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
    s = '{:08}'.format(entry['_id'])
    return os.path.join(root, s[0:2], s[2:4], s[4:6], s[6:8])


# Plac annotations for main function arguments
# .............................................................................
# Argument annotations are: (help, kind, abbrev, type, choices, metavar)
# Plac automatically adds a -h argument for help, so no need to do it here.

main.__annotations__ = dict(
    username       = ('use specified account user name',        'option', 'a', str),
    downloads_root = ('download directory root',                'option', 'd', str),
    file           = ('file containing repository identifiers', 'option', 'f'),
    id             = ('comma-separated list of repository ids', 'option', 'i'),
)


# Entry point
# .............................................................................

def cli_main():
    plac.call(main)

cli_main()
