import os
from lxml import etree
import luigi
import hashlib
from luigi.contrib.ssh import RemoteTarget, RemoteFileSystem
import datetime
from shepherd.tasks.common import logger

HERITRIX_CONFIG_ROOT=os.path.realpath(os.path.join(os.path.dirname(__file__),"../../../profiles"))
HERITRIX_PROFILE="%s/profile-domain.cxml" % HERITRIX_CONFIG_ROOT
HERITRIX_EXCLUDE="%s/exclude.txt" % HERITRIX_CONFIG_ROOT
HERITRIX_SHORTENERS="%s/url.shorteners.txt" % HERITRIX_CONFIG_ROOT
HERITRIX_SURTS="%s/surts.txt" % HERITRIX_CONFIG_ROOT

CLAMD_HOST='clamd'
CLAMD_PORT=3310


class DownloadGeolite2Database(luigi.Task):
    task_namespace = "dc"
    date = luigi.MonthParameter(default=datetime.datetime.today())

    download = "http://geolite.maxmind.com/download/geoip/database/GeoLite2-Country.tar.gz"
    match_glob = "GeoLite2-Country_*/GeoLite2-Country.mmdb"

    def output(self):
        return luigi.LocalTarget("GeoLite2-Country-%s.mmdb" % self.date)

    def run(self):
        os.system("curl -O %s" % self.download)
        os.system("tar xvfz GeoLite2-Country.tar.gz")
        os.system("cp %s %s" % ( self.match_glob, self.output().path))


class SyncLocalToRemote(luigi.Task):
    task_namespace = "sync"
    host = luigi.Parameter()
    input_task = luigi.TaskParameter()
    remote_path = luigi.Parameter()

    def requires(self):
        return self.input_task

    def complete(self):
        rt = RemoteTarget(host=self.host, path=self.remote_path)
        if not rt.exists():
            return False
        # Check hashes:
        local_target = self.input()
        with local_target.open('r') as reader:
            local_hash = hashlib.sha512(reader.read()).hexdigest()
            logger.info("LOCAL HASH: %s" % local_hash)
        # Read from Remote
        with rt.open('r') as reader:
            remote_hash = hashlib.sha512(reader.read()).hexdigest()
            logger.info("REMOTE HASH: %s" % remote_hash)

        # If they match, we are good:
        return remote_hash == local_hash

    def run(self):
        # Copy the local file over to the remote place
        rt = RemoteTarget(host=self.host, path=self.remote_path)
        rt.put(self.input().path)


class StaticLocalFile(luigi.ExternalTask):
    task_namespace = "sync"
    local_path = luigi.TaskParameter()

    def output(self):
        return luigi.LocalTarget(path=self.local_path)


class CreateDomainCrawlerBeans(luigi.Task):
    task_namespace = 'dc'
    job_name = luigi.Parameter()
    job_id = luigi.IntParameter()
    num_jobs = luigi.Parameter()
    amqp_host = luigi.Parameter(default="amqp.wa.bl.uk")

    def output(self):
        return luigi.LocalTarget("%s-%i.cxml" % (self.job_name, self.job_id))

    def run(self):
        """Creates the CXML content for a H3 job."""
        profile = etree.parse(HERITRIX_PROFILE)
        profile.xinclude()
        cxml = etree.tostring(profile, pretty_print=True, xml_declaration=True, encoding="UTF-8")
        logger.error("HERITRIX_PROFILE %s" % HERITRIX_PROFILE)
        logger.error("job_name %s" % self.job_name)
        cxml = cxml.replace("REPLACE_JOB_NAME", self.job_name)
        cxml = cxml.replace("REPLACE_LOCAL_NAME", self.job_id)
        cxml = cxml.replace("REPLACE_CRAWLER_COUNT", self.num_jobs)
        cxml = cxml.replace("REPLACE_CLAMD_HOST", CLAMD_HOST)
        cxml = cxml.replace("REPLACE_CLAMD_PORT", CLAMD_PORT)
        cxml = cxml.replace("REPLACE_AMQP_HOST", self.amqp_host)

        with self.output().open('w') as f:
            f.write(cxml)



class CreateDomainCrawlJobs(luigi.Task):
    task_namespace = 'dc'
    num_jobs = luigi.Parameter(default=4)
    host = luigi.Parameter()
    date = luigi.DateParameter(default=datetime.datetime.today())
    amqp_host = luigi.Parameter(default="amqp.wa.bl.uk")

    def requires(self):
        return SyncLocalToRemote( input_task=DownloadGeolite2Database(), host=self.host, remote_path="/dev/shm/GeoLite2-Country.mmdb")

    def run(self):
        for i in range(self.num_jobs):
            job_name = "dc%i-%s" % (i, self.date.strftime("%Y%m%d"))
            cxml_task = CreateDomainCrawlerBeans(job_name=job_name, job_id=i, num_jobs=self.num_jobs)
            yield SyncLocalToRemote( input_task=cxml_task, host=self.host, remote_path="/heritrix/jobs/%s/crawler-beans.cxml" % job_name)



if __name__ == '__main__':
    luigi.run(['dc.CreateDomainCrawlJobs', '--num-jobs', '4', '--host' , 'crawler04'])