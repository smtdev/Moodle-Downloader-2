import os
import ssl
import time
import urllib
import traceback
import threading
import contextlib

from pathlib import Path
import urllib.parse as urlparse

from utils.string_tools import StringTools
from state_recorder.course import Course
from state_recorder.file import File


class URLTarget(object):
    """
    URLTarget is responsible to download a special file.
    """

    def __init__(self, file: File, course: Course, destination: str,
                 token: str, thread_report: [], lock: threading.Lock,
                 ssl_context: ssl.SSLContext):
        """
        Initiating an URL target.
        """

        self.file = file
        self.course = course
        self.destination = destination
        self.token = token
        self.lock = lock
        self.ssl_context = ssl_context

        # get valid filename
        self.filename = StringTools.to_valid_name(self.file.content_filename)

        # Counts the download attempts
        self.url_tried = 0

        # To return errors
        self.success = False
        self.error = None

        # To create live reports.
        self.thread_id = 0
        self.thread_report = thread_report

        # Total downloaded.
        self.downloaded = 0

    def add_progress(self, count: int, block_size: int, total_size: int):
        """
        Callback function for urlretrieve to
        calculate the current download progress
        """
        self.thread_report[self.thread_id]['total'] += block_size
        self.downloaded += block_size

        # if urlretrieve cannot determine the actual download size,
        # use that of moodle.
        if(total_size == -1):
            total_size = self.file.content_filesize

        percent = 100
        if(total_size != 0):
            percent = int(self.downloaded * 100 / total_size)

        self.thread_report[self.thread_id]['percentage'] = percent

    def _add_token_to_url(self, url: str) -> str:
        """
        Adds the Moodle token to a URL
        @param url: The URL where the token should be added.
        @return: The URL with the token.
        """
        url_parts = list(urlparse.urlparse(url))
        query = dict(urlparse.parse_qsl(url_parts[4]))
        query.update({'token': self.token})
        url_parts[4] = urlparse.urlencode(query)
        return urlparse.urlunparse(url_parts)

    @staticmethod
    def _create_dir(path: str):
        # Creates the folders of a path if they do not exist.
        if(not os.path.exists(path)):
            try:
                # raise condition
                os.makedirs(path)
            except FileExistsError:
                pass

    def _rename_if_exists(self, path: str) -> str:
        """
        Rename a file name until no file with the same name exists.
        @param path: The path to the file to be renamed.
        @return: A path to a file that does not yet exist.
        """
        count = 1
        new_path = path
        content_filename = os.path.basename(path)
        destination = os.path.dirname(path)

        # this is some kind of raise condition
        # Even though it should hardly ever happen,
        # it is possible that threads try to create the same file
        self.lock.acquire()
        while os.path.exists(new_path):
            count += 1

            filename, file_extension = os.path.splitext(
                content_filename)

            new_filename = "%s_%02d%s" % (
                filename, count, file_extension)

            new_path = str(Path(destination) / new_filename)

        Path(new_path).touch()
        self.lock.release()

        return new_path

    def create_shortcut(self):
        """
        Creates a Shortcut to a URL
        Because shortcuts are different under Windows and Unix,
        both cases are covered here.
        """
        self.file.saved_to = str(Path(
            self.destination) / (self.filename + ".desktop"))
        if os.name == "nt":
            self.file.saved_to = str(Path(
                self.destination) / (self.filename + ".URL"))

        self.file.saved_to = self._rename_if_exists(self.file.saved_to)

        with open(self.file.saved_to, 'w+') as shortcut:
            if os.name == "nt":
                shortcut.write("[InternetShortcut]" + os.linesep)
                shortcut.write("URL=" + self.file.content_fileurl + os.linesep)
            else:
                shortcut.write("[Desktop Entry]" + os.linesep)
                shortcut.write("Encoding=UTF-8" + os.linesep)
                shortcut.write("Name=" + self.filename +
                               os.linesep)
                shortcut.write("Type=Link" + os.linesep)
                shortcut.write("URL=" + self.file.content_fileurl + os.linesep)
                shortcut.write("Icon=text-html" + os.linesep)
                shortcut.write("Name[en_US]=" + self.filename +
                               os.linesep)

        self.file.time_stamp = int(time.time())

        self.success = True

    def download(self, thread_id: int):
        """
        Downloads a file
        """
        self.thread_id = thread_id
        self.url_tried += 1

        # reset download status
        self.downloaded = 0
        self.thread_report[self.thread_id]['percentage'] = 0

        try:
            self._create_dir(self.destination)

            # if it is a URL we have to create a shortcut
            # instead of downloading it
            if (self.file.module_modname == 'url'):
                self.create_shortcut()
                return self.success

            self.file.saved_to = str(Path(self.destination) / self.filename)

            self.file.saved_to = self._rename_if_exists(self.file.saved_to)

            self.urlretrieve(self._add_token_to_url(
                self.file.content_fileurl),
                self.file.saved_to, context=self.ssl_context,
                reporthook=self.add_progress)

            self.file.time_stamp = int(time.time())

            self.success = True

        except Exception as e:
            self.error = traceback.format_exc() + "\nError:" + str(e)
            if (self.downloaded == 0 and
                # remove touched file
                    os.path.getsize(self.file.saved_to) == 0):
                os.remove(self.file.saved_to)
            else:
                # Subtract the already downloaded content in case of an error.
                self.thread_report[self.thread_id]['total'] -= self.downloaded
                self.thread_report[self.thread_id]['percentage'] = 100

        return self.success

    @staticmethod
    def urlretrieve(url: str, filename: str,
                    context: ssl.SSLContext, reporthook=None):
        """
        original source:
        https://github.com/python/cpython/blob/
        21bee0bd71e1ad270274499f9f58194ebb52e236/Lib/urllib/request.py#L229

        Because urlopen also supports context,
        I decided to adapt the download function.
        """
        url_parsed = urlparse.urlparse(url)

        with contextlib.closing(urllib.request.urlopen(url,
                                                       context=context)) as fp:
            headers = fp.info()

            # Just return the local path and the "headers" for file://
            # URLs. No sense in performing a copy unless requested.
            if url_parsed.scheme == "file" and not filename:
                return os.path.normpath(url_parsed.path), headers

            if not filename:
                raise RuntimeError("No filename specified!")

            tfp = open(filename, 'wb')

            with tfp:
                result = filename, headers

                # read overall
                read = 0

                # 4kb at once
                bs = 1024 * 8
                blocknum = 0

                # guess size
                size = -1
                if "content-length" in headers:
                    size = int(headers["Content-Length"])

                if reporthook:
                    reporthook(blocknum, bs, size)

                while True:
                    block = fp.read(bs)
                    if not block:
                        break
                    read += len(block)
                    tfp.write(block)
                    blocknum += 1
                    if reporthook:
                        reporthook(blocknum, bs, size)

        if size >= 0 and read < size:
            raise urllib.ContentTooShortError(
                "retrieval incomplete: got only %i out of %i bytes"
                % (read, size), result)

        return result

    def __str__(self):
        # URLTarget to string
        return 'URLTarget (%(file)s, %(success)s, %(error)s)' % {
            'file': self.file,
            'success': self.success, 'error': self.error}
