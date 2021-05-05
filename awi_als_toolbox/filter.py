# -*- coding: utf-8 -*-

"""
A module containing filter algorithm for the ALS point cloud data.
"""

__author__ = "Stefan Hendricks"


import numpy as np

import floenavi
from floenavi.polarstern import PolarsternAWIDashboardPos
from icedrift import GeoReferenceStation, IceCoordinateSystem, GeoPositionData
from datetime import datetime, timedelta
import os
from loguru import logger

class ALSPointCloudFilter(object):
    """ Base class for point cloud filters """

    def __init__(self, **kwargs):
        self.cfg = kwargs


class AtmosphericBackscatterFilter(ALSPointCloudFilter):
    """
    Identifies and removes target echoes that are presumably within the atmosphere
    based on elevation statistics for each line.
    """

    def __init__(self, filter_threshold_m=5):
        """
        Initialize the filter.
        :param filter_threshold_m:
        """
        super(AtmosphericBackscatterFilter, self).__init__(filter_threshold_m=filter_threshold_m)

    def apply(self, als):
        """
        Apply the filter for all lines in the ALS data container
        :param als:
        :return:
        """

        for line_index in np.arange(als.n_lines):

            # 1  Compute the median elevation of a line
            elevation = als.get("elevation")
            elevations = elevation[line_index, :]
            line_median = np.nanmedian(elevations)

            # 2. Fill nan values with median elevation
            # This is needed for spike detection
            elevations_nonan = np.copy(elevations)
            elevations_nonan[np.isnan(elevations_nonan)] = line_median

            # Search for sudden changes (spikes)
            spike_indices = self._get_filter_indices(elevations_nonan, self.cfg["filter_threshold_m"])

            # Remove spiky elevations
            elevation[line_index, spike_indices] = np.nan
            als.set("elevation", elevation)

    @staticmethod
    def _get_filter_indices(vector, filter_treshold):
        """ Compute the indices of potential spikes and save them in self.spike_indices """

        # Compute index-wise change in data
        diff = vector[1:] - vector[0:-1]

        # Compute change of data point to both directions
        diff_right = np.full(vector.shape, np.nan)
        diff_left = np.full(vector.shape, np.nan)
        diff_right[1:] = diff
        diff_left[0:-1] = diff

        # Check for data change exceeds the filter threshold
        right_threshold = np.abs(diff_right) > filter_treshold
        left_threshold = np.abs(diff_left) > filter_treshold

        # Check where data point is local extrema
        is_local_extrema = np.not_equal(diff_right > 0, diff_left > 0)
        condition1 = np.logical_and(right_threshold, left_threshold)

        # point is spike: if change on both sides exceeds threshold and is local
        # extrema
        is_spike = np.logical_and(condition1, is_local_extrema)
        spike_indices = np.where(is_spike)[0]

        return spike_indices


class IceDriftCorrection(ALSPointCloudFilter):
    """
    Corrects for ice drift during data aquisition, using floenavi or Polarstern position
    """

    def __init__(self,use_polarstern=False):
        """
        Initialize the filter.
        :param filter_threshold_m:
        """
        super(IceDriftCorrection, self).__init__(use_polarstern=use_polarstern)

    def apply(self, als):
        """
        Apply the filter for all lines in the ALS data container
        :param als:
        :return:
        """

        logger.info("IceDriftCorrection is applied")
        # 1. Initialise IceDriftStation
        self._get_IceDriftStation(als,use_polarstern=self.cfg["use_polarstern"])

        # 2. Initialise empty x,y arrays in als for the projection
        als.init_IceDriftCorrection()

        # 3. mask nan values for faster computation
        nonan = np.where(np.logical_or(np.isfinite(als.get("longitude")), np.isfinite(als.get("latitude"))))

        # 4. Generate GeoPositionData object from als
        time_als = np.array([datetime(1970,1,1,0,0,0) + timedelta(0,isec) for isec in als.get("timestamp")[nonan]])
        als_geo_pos = GeoPositionData(time_als,als.get("longitude")[nonan],als.get("latitude")[nonan])

        # 5. Compute projection
        icepos = self.IceCoordinateSystem.get_xy_coordinates(als_geo_pos)

        # 6. Store projected coordinates
        als.x[nonan] = icepos.xc
        als.y[nonan] = icepos.yc

        # 7. Set IceDriftCorrected
        als.IceDriftCorrected = True
        als.IceCoordinateSystem = self.IceCoordinateSystem


    def _get_IceDriftStation(self,als,use_polarstern=False):
        # Check for master solutions of Leg 1-3 in floenavi package
        path_data = os.path.join('/'.join(floenavi.__file__.split('/')[:-2]),'data/master-solution')
        ms_sol = np.array([ifile for ifile in os.listdir(path_data) if ifile.endswith('.csv')])
        ms_sol_dates = np.array([[datetime.strptime(ifile.split('-')[2],'%Y%m%d'),
                                  datetime.strptime(ifile.split('-')[3],'%Y%m%d')] for ifile in ms_sol])
        ind_begin = np.where(np.logical_and(als.tcs_segment_datetime>=ms_sol_dates[:,0],
                                            als.tcs_segment_datetime<=ms_sol_dates[:,1]))[0]
        ind_end   = np.where(np.logical_and(als.tce_segment_datetime>=ms_sol_dates[:,0],
                                            als.tce_segment_datetime<=ms_sol_dates[:,1]))[0]
        self.read_floenavi = False
        if not use_polarstern:
            if ind_begin.size>0 and ind_end.size>0:
                if ind_begin==ind_end:
                    self.read_floenavi = True
                
        if self.read_floenavi:
            refstat_csv_file = os.path.join(path_data,ms_sol[ind_begin][0])
            refstat = GeoReferenceStation.from_csv(refstat_csv_file)
        else:
            refstat = PolarsternAWIDashboardPos(als.tcs_segment_datetime,als.tce_segment_datetime).reference_station
        
        self.IceCoordinateSystem = als.IceCoordinateSystem = IceCoordinateSystem(refstat)
