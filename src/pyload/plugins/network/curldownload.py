# -*- coding: utf-8 -*-
# @author: RaNaN

from __future__ import absolute_import, division, unicode_literals

import io
import os
import shutil
from builtins import range
from contextlib import closing
from time import time

from future import standard_library
standard_library.install_aliases()

import pycurl
from pyload.core.datatype import Connection
from pyload.core.network import CookieJar
from pyload.plugins import Abort
from pyload.utils import format
from pyload.utils.path import remove

from .curlchunk import ChunkInfo, CurlChunk
from .curlrequest import ResponseException
from .download import Download


# TODO: save content-disposition for resuming
class CurlDownload(Download):
    """
    Loads an url, http + ftp supported.
    """

    # def __init__(self, url, filename, get={}, post={}, referer=None, cj=None, bucket=None,
    #              options={}, disposition=False):

    CONTEXT_CLASS = CookieJar

    def __init__(self, *args, **kwargs):
        Download.__init__(self, *args, **kwargs)

        self.path = None
        self.disposition = False

        self.chunks = []
        self.chunk_support = None

        self.manager = pycurl.CurlMulti()

        # needed for speed calculation
        self.last_arrived = []
        self.speeds = []
        self.last_speeds = [0, 0]

    @property
    def speed(self):
        last = (sum(x) for x in self.last_speeds if x)
        return (sum(self.speeds) + sum(last)) // (1 + len(last))

    @property
    def arrived(self):
        return sum(c.arrived for c in self.chunks) if self.chunks else self._size

    @property
    def name(self):
        return self._name if self.disposition else None

    def _copy_chunks(self):
        init = format.path(self.info.get_chunk_name(0))  #: initial chunk name

        if self.info.get_count() > 1:
            with io.open(init, "rb+") as fo:  #: first chunkfile
                for i in range(1, self.info.get_count()):
                    # input file
                    fo.seek(
                        self.info.get_chunk_range(i - 1)[1] + 1)  #: seek to beginning of chunk, to get rid of overlapping chunks
                    fname = format.path("{0}.chunk{1:d}".format(self.path, i))
                    buf = 32 << 10
                    with io.open(fname, mode='rb') as fi:
                        while True:  #: copy in chunks, consumes less memory
                            data = fi.read(buf)
                            if not data:
                                break
                            fo.write(data)

                    if fo.tell() < self.info.get_chunk_range(i)[1]:
                        remove(init)
                        self.info.remove()  #: there are probably invalid chunks
                        raise Exception(
                            "Downloaded content was smaller than expected. Try to reduce download connections")
                    remove(fname)  #: remove chunk

        if self.name:
            self.path = format.path(os.path.dirname(self.path), self.name)

        shutil.move(init, format.path(self.path))
        self.info.remove()  #: remove info file

    def check_resume(self):
        try:
            self.info = ChunkInfo.load(self.path)
            self.info.resume = True  #: resume is only possible with valid info file
            self._size = self.info.size
            self.info_saved = True
        except IOError:
            self.info = ChunkInfo(self.path)

    def download(self, uri, path, get={}, post={}, referer=True,
                 disposition=False, chunks=1, resume=False, cookies=True):
        """
        Returns new filename or None.
        """
        self.url = uri
        self.path = path
        self.disposition = disposition
        self.get = get
        self.post = post
        self.referer = referer
        self.cookies = cookies

        self.check_resume()
        chunks = max(1, chunks)
        resume = self.info.resume and resume

        try:
            self._download(chunks, resume)
        except pycurl.error as e:
            # code 33 - no resume
            code = e.args[0]
            if code == 33:
                # try again without resume
                self.pyload.log.debug("Errno 33 -> Restart without resume")

                # remove old handles
                for chunk in self.chunks:
                    self.close_chunk(chunk)

                return self._download(chunks, False)
            else:
                raise
        finally:
            self.close()

        return self.name

    def _download(self, chunks, resume):
        if not resume:
            self.info.clear()
            self.info.add_chunk("{0}.chunk0".format(
                self.path), (0, 0))  #: create an initial entry

        self.chunks = []

        # initial chunk that will load complete file (if needed)
        init = CurlChunk(0, self, None, resume)

        self.chunks.append(init)
        self.manager.add_handle(init.get_handle())

        last_finish_check = 0
        last_time_check = 0
        chunks_done = set()  #: list of curl handles that are finished
        chunks_created = False
        done = False
        if self.info.get_count() > 1:  #: This is a resume, if we were chunked originally assume still can
            self.chunk_support = True

        while True:
            # need to create chunks
            if not chunks_created and self.chunk_support and self.size:  #: will be set later by first chunk

                self.flags ^= Connection.Resumable
                if not resume:
                    self.info.set_size(self.size)
                    self.info.create_chunks(chunks)
                    self.info.save()

                chunks = self.info.get_count()

                init.set_range(self.info.get_chunk_range(0))

                for i in range(1, chunks):
                    c = CurlChunk(
                        i, self, self.info.get_chunk_range(i), resume)

                    handle = c.get_handle()
                    if handle:
                        self.chunks.append(c)
                        self.manager.add_handle(handle)
                    else:
                        # close immediately
                        self.pyload.log.debug("Invalid curl handle -> closed")
                        c.close()

                chunks_created = True

            while True:
                ret, num_handles = self.manager.perform()
                if ret != pycurl.E_CALL_MULTI_PERFORM:
                    break

            t = time()

            # reduce these calls
            # when num_q is 0, the loop is exited
            while last_finish_check + 0.5 < t:
                # list of failed curl handles
                failed = []
                ex = None  #: save only last exception, we can only raise one anyway

                num_q, ok_list, err_list = self.manager.info_read()
                for c in ok_list:
                    chunk = self.find_chunk(c)
                    try:  #: check if the header implies success, else add it to failed list
                        chunk.verify_header()
                    except ResponseException as e:
                        self.pyload.log.debug(
                            "Chunk {0:d} failed: {1}".format(chunk.id + 1, str(e)))
                        failed.append(chunk)
                        ex = e
                    else:
                        chunks_done.add(c)

                for c in err_list:
                    curl, errno, msg = c
                    chunk = self.find_chunk(curl)
                    # test if chunk was finished
                    if errno != 23 or "0 !=" not in msg:
                        failed.append(chunk)
                        ex = pycurl.error(errno, msg)
                        self.pyload.log.debug(
                            "Chunk {0:d} failed: {1}".format(chunk.id + 1, ex))
                        continue

                    try:  #: check if the header implies success, else add it to failed list
                        chunk.verify_header()
                    except ResponseException as e:
                        self.pyload.log.debug(
                            "Chunk {0:d} failed: {1}".format(chunk.id + 1, str(e)))
                        failed.append(chunk)
                        ex = e
                    else:
                        chunks_done.add(curl)
                if not num_q:  #: no more info to get

                    # check if init is not finished so we reset download connections
                    # note that other chunks are closed and everything
                    # downloaded with initial connection
                    if failed and init not in failed and init.c not in chunks_done:
                        self.pyload.log.error(
                            _("Download chunks failed, fallback to single connection | {0}".format(ex)))

                        # list of chunks to clean and remove
                        to_clean = [x for x in self.chunks if x is not init]
                        for chunk in to_clean:
                            self.close_chunk(chunk)
                            self.chunks.remove(chunk)
                            remove(
                                format.path(
                                    self.info.get_chunk_name(
                                        chunk.id)))

                        # let first chunk load the rest and update the info
                        # file
                        init.reset_range()
                        self.info.clear()
                        self.info.add_chunk("{0}.chunk0".format(
                            self.path), (0, self.size))
                        self.info.save()
                    elif failed:
                        raise ex

                    last_finish_check = t

                    if len(chunks_done) >= len(self.chunks):
                        if len(chunks_done) > len(self.chunks):
                            self.pyload.log.warning(
                                _("Finished download chunks size incorrect, please report bug"))
                        done = True  #: all chunks loaded

                    break

            if done:
                break  #: all chunks loaded

            # calc speed once per second, averaging over 3 seconds
            if last_time_check + 1 < t:
                diff = [c.arrived - (self.last_arrived[i] if len(self.last_arrived) > i else 0) for i, c in
                        enumerate(self.chunks)]

                self.last_speeds[1] = self.last_speeds[0]
                self.last_speeds[0] = self.speeds
                self.speeds = [float(a) // (t - last_time_check) for a in diff]
                self.last_arrived = [c.arrived for c in self.chunks]
                last_time_check = t

            if self.do_abort:
                raise Abort

            self.manager.select(1)

        for chunk in self.chunks:
            chunk.flush_file()  #: make sure downloads are written to disk

        self._copy_chunks()

    def find_chunk(self, handle):
        """
        Linear search to find a chunk (should be ok since chunk size is usually low).
        """
        for chunk in self.chunks:
            if chunk.c == handle:
                return chunk

    def close_chunk(self, chunk):
        try:
            self.manager.remove_handle(chunk.c)
        except pycurl.error as e:
            self.pyload.log.debug("Error removing chunk: {0}".format(str(e)))
        finally:
            chunk.close()

    def close(self):
        """
        Cleanup.
        """
        for chunk in self.chunks:
            self.close_chunk(chunk)

        # Workaround: pycurl segfaults when closing multi, that never had
        # any curl handles
        if hasattr(self, 'manager'):
            with closing(pycurl.Curl()) as c:
                self.manager.add_handle(c)
                self.manager.remove_handle(c)

        self.chunks = []
        if hasattr(self, 'manager'):
            self.manager.close()
            del self.manager
        if hasattr(self, "info"):
            del self.info
