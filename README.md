CASICS Downloader
================

<img width="100px" align="right" src=".graphics/casics-logo-small.svg">

A module to download repositories from GitHub.

*Authors*:      [Michael Hucka](http://github.com/mhucka) and [Matthew J. Graham](https://github.com/doccosmos)<br>
*Repository*:   [https://github.com/casics/downloader](https://github.com/casics/downloader)<br>
*License*:      Unless otherwise noted, this content is licensed under the [GPLv3](https://www.gnu.org/licenses/gpl-3.0.en.html) license.

☀ Introduction
-----------------------------

This module in CASICS is used to download copies of repositories from GitHub.  It is meant to be run on computers that have a common shared file system with each other and the core CASICS server, so that the repositories can all be stored in a single directory tree.

The directory tree used to store repositories is hierarchically structured in such a way that the number of individual directories at each level is limited to a small number.  This prevents problems that very large numbers of directories can cause to some programs.  GitHub repositories are mapped to this tree according to their integer identifiers like this:

```
nn/nn/nn/nn
```

where each `n` is an integer `0`..`9`.  For example, the following are 3 examples of repository paths stored according this scheme:

```
00/00/00/01
00/00/00/62
00/15/63/99
```

The full number read left to right (without the slashes) is the identifier of the repository (which is the same as the database key in our database).  The numbers are zero-padded.  So for example, repository entry #`7182480` leads to a path of `07/18/24/80`.

⁇ Getting help and support
--------------------------

If you find an issue, please submit it in [the GitHub issue tracker](https://github.com/casics/downloader/issues) for this repository.

♬ Contributing &mdash; info for developers
------------------------------------------

A lot remains to be done on CASICS in many areas.  We would be happy to receive your help and participation if you are interested.  Please feel free to contact the developers either via GitHub or the mailing list [casics-team@googlegroups.com](casics-team@googlegroups.com).

Everyone is asked to read and respect the [code of conduct](CONDUCT.md) when participating in this project.

❤️ Acknowledgments
------------------

Funding for this and other CASICS work has come from the [National Science Foundation](https://nsf.gov) via grant NSF EAGER #1533792 (Principal Investigator: Michael Hucka).
