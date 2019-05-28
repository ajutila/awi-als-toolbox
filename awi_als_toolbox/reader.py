# -*- coding: utf-8 -*-

"""
"""

__author__ = "Stefan Hendricks"

import numpy as np

from collections import OrderedDict

import struct
import logging


class AirborneLaserScannerFile(object):
    """ Class to retrieve data from a AWI ALS binary data file """

    # Variable names and their data type
    line_variables = OrderedDict((('timestamp', np.float64),
                                  ('longitude', np.float64),
                                  ('latitude', np.float64),
                                  ('elevation', np.float64),
                                  ('amplitude', np.float32),
                                  ('reflectance', np.float32)))


    def __init__(self, filepath):
        """
        Connects to a AWI binary ALS data file. The data is not parsed into memory when this is class is called,
        only the header information that is necessary to decode the binary data structure.

        Usage:
        ======

            alsfile = AirborneLaserScannerFile(filename)
            als = alsfile.get_data(start, stop)

        :param filepath: (str) The path of the AWI ALS file
        """

        # Store Parameter
        self.filepath = filepath

        # Decode and store header information
        self.header = ALSFileHeader(filepath)
        self.line_timestamp = None

        # Read the line timestamp
        # -> on timestamp per line to later select subsets of the full content
        self._read_line_timestamp()

    def get_data(self, start_seconds=None, end_seconds=None):
        """
        Read a subset of the ALS data and return its content. The subset is selected with the (integer) seconds of
        the day. If `start_seconds` and `end_seconds` are omitted, the maximum range will be used
        :param start_seconds: (int) Start of the subset in seconds of the day
        :param end_seconds: (int) End of the subset in seconds of the day
        :return: an ALSData object containing the data subset
        """

        # Check input
        if start_seconds is None:
            start_seconds = self.line_timestamp[0]

        if end_seconds is None:
            end_seconds = self.line_timestamp[-1]

        # Sanity check
        self.validate_time_range(start_seconds, end_seconds)

        # Get the number of lines
        line_index = [
            np.where(self.line_timestamp >= start_seconds)[0][0],
            np.where(self.line_timestamp <= end_seconds)[0][-1]]
        n_selected_lines = line_index[1] - line_index[0]

        # Get the section of the file to read
        startbyte, nbytes = self._get_data_bytes(line_index)

        # Get the shape of the output array
        nlines, nshots = n_selected_lines, self.header.data_points_per_line

        # Init the data output
        als = ALSData(self.line_variables, (nlines, nshots))

        # Read the binary data
        bindat = np.ndarray(shape=(nlines), dtype=object)
        with open(self.filepath, 'rb') as f:
            for i in np.arange(n_selected_lines):
                f.seek(startbyte)
                bindat[i] = f.read(nbytes)
                startbyte += nbytes

        # Unpack the binary data
        start_byte, stop_byte = 0, self.header.bytes_per_line
        for i in np.arange(nlines):
            line = bindat[i]
            i0, i1 = 0, 8*nshots
            als.timestamp[i, :] = struct.unpack(">{n}d".format(n=nshots), line[i0:i1])
            i0 = i1
            i1 = i0 + 8*nshots
            als.latitude[i, :] = struct.unpack(">{n}d".format(n=nshots), line[i0:i1])
            i0 = i1
            i1 = i0 + 8*nshots
            als.longitude[i, :] = struct.unpack(">{n}d".format(n=nshots), line[i0:i1])
            i0 = i1
            i1 = i0 + 8*nshots
            start_byte += self.header.bytes_per_line
            stop_byte += self.header.bytes_per_line
            als.elevation[i, :] = struct.unpack(">{n}d".format(n=nshots), line[i0:i1])

        # All done, return
        return als

    def validate_time_range(self, start, stop):
        """ Check for oddities in the time range selection """
        fstart = self.line_timestamp[0]
        fstop = self.line_timestamp[-1]

        # Raise Errors
        if start > stop:
            msg = "start time {start} after stop time {stop}".format(start=start, stop=stop)
            raise ValueError(msg)
        if start > fstop or stop < fstart:
            msg = "time range {start} - {stop} out of bounds {fstart} - {fstop}"
            msg = msg.format(start=start, stop=stop, fstart=fstart, fstop=fstop)
            raise ValueError(msg)

        # Raise Warnings
        if start < fstart:
            # TODO: Use logging
            logging.warn("start time {start} before actual start of file {fstart}".format(start=start, fstart=fstart))
        if stop > fstop:
            logging.warn("stop time {stop} after actual end of file {fstop}".format(stop=stop, fstop=fstop))

    def _get_data_bytes(self, line_index):
        """
        Computes the start byte and the number of bytes to read for the given lines
        :param line_index: (array) list of scan lines to be read
        :return: (int, int) startbyte and the number of bytes for the data subset
        """

        # Start byte of scan line
        startbyte = np.uint32(self.header.byte_size)
        startbyte += np.uint32(self.header.bytes_sec_line)
        startbyte += np.uint32(line_index[0]) * np.uint32(self.header.bytes_per_line)

        # Number bytes for selected scan lines
        nbytes = self.header.bytes_per_line

        return startbyte, nbytes

    def _read_line_timestamp(self):
        """ Read the line time stamp """
        with open(self.filepath, 'rb') as f:
            f.seek(self.header.byte_size)
            data = f.read(self.header.bytes_sec_line)
        struct_def = ">{scan_lines}L".format(scan_lines=self.header.scan_lines)
        self.line_timestamp = np.array(struct.unpack(struct_def, data))


class ALSFileHeader(object):
    """ Class for parsing and storing header information of binary AWI ALS data files """

    # Header information of the form (variable_name, [number of bytes, struct format])
    header_dict = OrderedDict((('scan_lines', [4, '>L']),
                               ('data_points_per_line', [2, '!H']),
                               ('bytes_per_line', [2, '>H']),
                               ('bytes_sec_line', [8, '>Q']),
                               ('year', [2, '>H']),
                               ('month', [1, '>b']),
                               ('day', [1, '>b']),
                               ('start_time_sec', [4, '>L']),
                               ('stop_time_sec', [4, '>L']),
                               ('device_name', [8, '>8s'])))

    def __init__(self, filepath, verbose=True):
        """
        Decode and store header information from binary AWI ALS files
        :param filepath: (str) The path to the ALS file
        :param verbose: (bool) Flag determining the verbosity
        """

        # Read the header
        with open(filepath, 'rb') as f:

            # Read header size
            self.byte_size = struct.unpack('>b', f.read(1))[0]
            logging.info("als_header.byte_size: %s" % str(self.byte_size))
            if self.byte_size == 36:
                self.header_dict['data_points_per_line'] = [1, '>B']
            elif self.byte_size == 37:
                self.header_dict['data_points_per_line'] = [2, '>H']
            else:
                msg = "Unkown ALS L1B header size: %g (Should be 36 or 37 or unsupported Device)"
                msg = msg % self.byte_size,
                raise ValueError(msg)

            # Read Rest of header
            for key in self.header_dict.keys():
                nbytes, fmt = self.header_dict[key][0], self.header_dict[key][1]
                setattr(self, key, struct.unpack(fmt, f.read(nbytes))[0])
                if verbose:
                    logging.info("als_header.%s: %s" % (key, str(getattr(self, key))))

    @property
    def center_beam_index(self):
        """ Returns index of the center beam """
        return np.median(np.arange(self.data_points_per_line))


class ALSData(object):
    """ A data class container for ALS data"""

    def __init__(self, vardef, shape):
        """
        Data container for ALS data ordered in scan lines.
        NOTE: Upon initialization this container will be empty. The content must be added directly.
        :param filedef: (dict) Variable definition {varname: dtype, ... }
        :param shape: The shape of the (nlines, nshots) of the data
        """

        # Store arguments
        self.vardef = vardef
        self.shape = shape

        # Create the array entries
        for key in vardef.keys():
            setattr(self, key, np.ndarray(shape=shape, dtype=vardef[key]))