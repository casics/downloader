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
import magic
import locale
import http
import urllib
import requests
import shutil
import datetime
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

def main(downloads_root=default_download_dir, file=None, id=None, user_login=None):
    '''Downloads copies of respositories.'''
    if id:
        id_list = [int(id)]
    elif file:
        with open(file) as f:
            id_list = [int(x) for x in f.read().splitlines()]
    else:
        msg('Need to provide a list of what to download')
        return
    (login, password) = get_account_info(user_login)
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    download(downloads_root, id_list, login, password)


def download(downloads_root, id_list, login, password):
    dbinterface = Database()
    db = dbinterface.open()

    repo_iterator = iter(id_list)
    total = len(id_list)

    downloads_tmp = os.path.join(downloads_root, 'tmp')
    os.makedirs(downloads_tmp, exist_ok=True)

    msg('Starting to download {} repos to {}'.format(total, downloads_root))
    msg('Using temporary directory in {}'.format(downloads_tmp))
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
                failures = 0
                continue

            # Try first with the default master branch.
            count += 1
            outfile = None
            try:
                url = "https://github.com/{}/{}/archive/master.zip".format(
                    entry.owner, entry.name)
                outfile = wget.download(url, bar=None, out=downloads_tmp)
            except Exception as e:
                # If we get a 404 from GitHub, it may mean there is no "master".
                # To find out what it really is, we first try scraping the web
                # page, and if that fails, we resort to using an API call.
                if e.code == 404:
                    newurl = get_archive_url_by_scraping(entry)
                    if newurl:
                        try:
                            outfile = wget.download(newurl, bar=None, out=downloads_tmp)
                        except Exception as newe:
                            msg('Failed to download {}: {}'.format(entry.id, str(newe)))
                            failures += 1
                            continue
                    else:
                        newurl = get_archive_url_by_api(entry, login, password)
                        if newurl:
                            try:
                                outfile = wget.download(newurl, bar=None, out=downloads_tmp)
                            except Exception as newe:
                                msg('Failed to download {}: {}'.format(entry.id, str(newe)))
                                failures += 1
                                continue
                        else:
                            msg("Couldn't find download URL for #{} ({}/{})".format(
                                entry.id, entry.owner, entry.name))
                            continue
                else:
                    msg('Failed to download {}: {}'.format(entry.id, str(e)))
                    failures += 1
                    continue

            # Unzip it to a temporary path, then move it to the final location.

            filesize = file_size(outfile)
            try:
                outdir = unzip_archive(outfile, downloads_tmp)
                os.remove(outfile)
            except Exception as e:
                msg('{} left zipped: {}'.format(outfile, str(e)))
                failures += 1
                continue

            os.makedirs(os.path.dirname(localpath), exist_ok=True)
            os.rename(outdir, localpath)

            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            msg('{}/{} (#{} in {}, zip size {}, finished at {})'.format(
                entry.owner, entry.name, key, localpath, filesize, now))
            if count % 100 == 0:
                msg('{} [{:2f}]'.format(count, time() - start))
                start = time()
            failures = 0
        except StopIteration:
            break
        except Exception as err:
            msg('Exception: {0}'.format(err))
            continue

        if failures >= max_failures:
            msg('Stopping because of too many repeated failures.')
            break

    dbinterface.close()
    msg('')
    msg('Done.')


# Helpers
# .............................................................................

def zip_file_exists(path, dir):
    # This is the path that GitHub creates for zip files
    filename = path[path.rfind('/') + 1 :] + '-master.zip'
    return os.path.exists(dir + '/' + path + '/' + filename)


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
        name = component.filename.encode('utf-8').decode('utf-8')
        dir_name = os.path.join(dest, os.path.dirname(name))
        full_path = os.path.join(dir_name, os.path.basename(name))

        # Create missing directories.
        try:
            os.makedirs(dir_name)
        except OSError as e:
            if e.errno == os.errno.EEXIST:
                pass
            else:
                raise
        except Exception as e:
            raise

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
    return (magic.from_buffer(content).decode("utf-8").find('text') >= 0)


def file_size(path):
    return humanize.naturalsize(os.path.getsize(path))


def get_account_info(user_login=None):
    cfg = Config()
    section = Host.name(Host.GITHUB)
    try:
        if user_login:
            for name, value in cfg.items(section):
                if name.startswith('login') and value == user_login:
                    login = user_login
                    index = name[len('login'):]
                    if index:
                        password = cfg.get(section, 'password' + index)
                    else:
                        # login entry doesn't have an index number.
                        # Might be a config file in the old format.
                        password = value
                    break
            # If we get here, we failed to find the requested login.
            msg('Cannot find "{}" in section {} of config.ini'.format(
                user_login, section))
        else:
            try:
                login = cfg.get(section, 'login1')
                password = cfg.get(section, 'password1')
            except:
                login = cfg.get(section, 'login')
                password = cfg.get(section, 'password')
    except Exception as err:
        msg(err)
        text = 'Failed to read "login" and/or "password" for {}'.format(
            section)
        raise SystemExit(text)
    return (login, password)


def get_home_page_text(entry):
    url = 'http://github.com/' + entry.owner + '/' + entry.name
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


def get_archive_url_by_api(entry, login, password):
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
    user_login     = ('use specified account login',            'option', 'a', str),
    downloads_root = ('download directory root',                'option', 'd', str),
    file           = ('file containing repository identifiers', 'option', 'f'),
    id             = ('(single) repository identifier',         'option', 'i'),
)


# Entry point
# .............................................................................

def cli_main():
    plac.call(main)

cli_main()
