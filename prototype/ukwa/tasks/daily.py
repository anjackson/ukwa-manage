#!/usr/bin/env python
# encoding: utf-8
"""
This module summarises the tasks that are to be run daily.
"""

import luigi
from ukwa.tasks.hadoop.hdfs import GenerateHDFSSummaries
from ukwa.tasks.backup.postgresql import BackupProductionW3ACTPostgres
from ukwa.tasks.access.search import PopulateBetaCollectionsSolr
#from ukwa.tasks.access.turing import ListFilesToUploadToAzure


class DailyIngestTasks(luigi.WrapperTask):
    """
    Daily ingest tasks, should generally be a few hours ahead of the access-side tasks (below):
    """
    def requires(self):
        return [BackupProductionW3ACTPostgres(),
                GenerateHDFSSummaries()]


class DailyAccessTasks(luigi.WrapperTask):
    """
    Daily access tasks. May depend on the ingest tasks, but will usually run from the access server,
    so can't be done in the one job. To be run an hour or so after the :py:DailyIngestTasks.
    """
    def requires(self):
        return [PopulateBetaCollectionsSolr()]
