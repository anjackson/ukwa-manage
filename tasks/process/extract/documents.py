import re
import os
import json
import hashlib
import logging
from urlparse import urlparse
import requests
from requests.utils import quote
import xml.dom.minidom
import luigi.contrib.hdfs
import luigi.contrib.hadoop

import crawl
from crawl.w3act.w3act import w3act
from crawl.h3.utils import url_to_surt
from crawl.dex.document_mdex import DocumentMDEx
import tasks
from tasks.crawl.h3.crawl_job_tasks import CrawlFeed
from tasks.process.hadoop.crawl_summary import ScanForOutputs
from tasks.common import target_name

logger = logging.getLogger('luigi-interface')

HDFS_PREFIX = os.environ.get("HDFS_PREFIX", "")
WAYBACK_PREFIX = os.environ.get("WAYBACK_PREFIX", "http://localhost:9080/wayback")

LUIGI_STATE_FOLDER = os.environ['LUIGI_STATE_FOLDER']
ACT_URL = os.environ['ACT_URL']
ACT_USER = os.environ['ACT_USER']
ACT_PASSWORD = os.environ['ACT_PASSWORD']


def dtarget(job, launch_id, status):
    return luigi.LocalTarget('{}/{}'.format(LUIGI_STATE_FOLDER, target_name('logs/documents', job, launch_id, status)))


class LogFilesForJobLaunch(luigi.Task):
    """
    On initialisation, looks up all logs current on HDFS for a particular job.

    Emits list of files into an appropriate state folder.
    """
    task_namespace = 'scan'
    job = luigi.Parameter()
    launch_id = luigi.Parameter()

    output_list = []

    def get_output_list(self):
        if len(self.output_list) > 0:
            return
        # Get HDFS client:
        client = luigi.contrib.hdfs.get_autoconfig_client()
        # Look for log files:
        outputs = {}
        is_final = False
        parent_path = "%s/heritrix/output/logs/%s/%s" % (HDFS_PREFIX, self.job, self.launch_id)
        for list_item in client.listdir(parent_path):
            item = os.path.basename(list_item)
            item_path = os.path.join(parent_path,item)
            if item == "crawl.log":
                logger.debug("Processing %s, %s" % (item, item_path))
                is_final = True
                outputs["final"] = item_path
            elif item.endswith(".lck"):
                pass
            elif item.startswith("crawl.log"):
                logger.debug("Processing %s, %s" % (item, item_path))
                outputs[item[-14:]] = item_path
            else:
                logger.debug("Skipping %s, %s" % (item, item_path))
        # Just store the values:
        self.output_list = sorted(outputs.values())

    def output(self):
        self.get_output_list()
        return luigi.LocalTarget(path="%s/log-files-%s-%s-%i.txt"
                                             % (LUIGI_STATE_FOLDER, self.job, self.launch_id, len(self.output_list)))
        #return luigi.contrib.hdfs.HdfsTarget(path="%s/log-files-%s-%s-%i.txt"
        #                                     % (LUIGI_STATE_FOLDER, self.job, self.launch_id, len(self.output_list)))

    def run(self):
        self.get_output_list()
        with self.output().open('w') as out_file:
            for output in self.output_list:
                logger.info("Writing %s" % output)
                out_file.write('{}\n'.format(output))


class ScanLogFileForDocs(luigi.Task):
    """Watched the crawled documents log queue and passes entries to w3act
    Input:
    {
        "annotations": "ip:173.236.225.186,duplicate:digest",
        "content_digest": "sha1:44KA4PQA5TYRAXDIVJIAFD72RN55OQHJ",
        "content_length": 324,
        "extra_info": {},
        "hop_path": "IE",
        "host": "acid.matkelly.com",
        "jobName": "frequent",
        "mimetype": "text/html",
        "seed": "WTID:12321444",
        "size": 511,
        "start_time_plus_duration": "20160127211938966+230",
        "status_code": 404,
        "thread": 189,
        "timestamp": "2016-01-27T21:19:39.200Z",
        "url": "http://acid.matkelly.com/img.png",
        "via": "http://acid.matkelly.com/",
        "warc_filename": "BL-20160127211918391-00001-35~ce37d8d00c1f~8443.warc.gz",
        "warc_offset": 36748
    }
    Note that 'seed' is actually the source tag, and is set up to contain the original (Watched) Target ID.
    Output:
    [
    {
    "id_watched_target":<long>,
    "wayback_timestamp":<String>,
    "landing_page_url":<String>,
    "document_url":<String>,
    "filename":<String>,
    "size":<long>
    },
    <further documents>
    ]
    See https://github.com/ukwa/w3act/wiki/Document-REST-Endpoint
    i.e.
    seed -> id_watched_target
    start_time_plus_duration -> wayback_timestamp
    via -> landing_page_url
    url -> document_url (and filename)
    content_length -> size
    Note that, if necessary, this process to refer to the
    cdx-server and wayback to get more information about
    the crawled data and improve the landing page and filename data.
    """
    task_namespace = 'doc'
    job = luigi.Parameter()
    launch_id = luigi.Parameter()

    def requires(self):
        return { 'feed': CrawlFeed(self.job), 'logs': LogFilesForJobLaunch(self.job, self.launch_id) }

    def output(self):
        return dtarget(self.job, self.launch_id, "docs-found")

    def run(self):
        # Setup...
        watched_surts = self.load_watched_surts()
        # Get HDFS client:
        client = luigi.contrib.hdfs.get_autoconfig_client()
        # open the output file:
        with self.output().open('w') as out_file:
            # loop over log files:
            for log_file_path in self.input()['logs'].open('r'):
                log_file_path = log_file_path.strip()
                logger.info("Processing log file: %s..." % log_file_path)
                # Get HDFS Input
                #log_file = luigi.contrib.hdfs.HdfsTarget(path=log_file_path, format=Plain)
                # Then scan the logs for documents:
                line_count = 0
                with client.client.read(hdfs_path=log_file_path) as f:
                    for line in f:
                        if line_count % 100 == 0:
                            self.set_status_message = "Currently at line %i of file %s" % (line_count, log_file_path)
                            logger.info(self.set_status_message)
                        line_count += 1
                        # And yield tasks for each relevant document:
                        (timestamp, status_code, content_length, url, hop_path, via, mime,
                         thread, start_time_plus_duration, hash, source, annotations) = re.split(" +", line, maxsplit=11)
                        # Skip non-downloads:
                        if status_code == '-' or status_code == '' or int(status_code) / 100 != 2:
                            continue
                        # Check the URL and Content-Type:
                        if "application/pdf" in mime:
                            for prefix in watched_surts:
                                document_surt = url_to_surt(url)
                                landing_page_surt = url_to_surt(via)
                                # Are both URIs under the same watched SURT:
                                if document_surt.startswith(prefix) and landing_page_surt.startswith(prefix):
                                    logger.info("Found document: %s" % line)
                                    # Proceed to extract metadata and pass on to W3ACT:
                                    doc = {
                                        'wayback_timestamp': start_time_plus_duration[:14],
                                        'landing_page_url': via,
                                        'document_url': url,
                                        'filename': os.path.basename(urlparse(url).path),
                                        'size': int(content_length),
                                        # Add some more metadata to the output so we can work out where this came from:
                                        'job_name': self.job,
                                        'launch_id': self.launch_id,
                                        'source': source
                                    }
                                    logger.info("Found document: %s" % doc)
                                    # And write out to the result file:
                                    out_file.write('{}\t{}'.format(url, json.dumps(doc)))

    def load_watched_surts(self):
        # First find the watched seeds list:
        targets = json.load(self.input()['feed'].open('r'))
        watched = []
        for t in targets:
            if t['watched']:
                for seed in t['seeds']:
                    watched.append(seed)
        # Convert to SURT form:
        watched_surts = set()
        for url in watched:
            watched_surts.add(url_to_surt(url))
        logger.info("WATCHED SURTS %s" % watched_surts)
        return watched_surts


class AvailableInWayback(luigi.ExternalTask):
    """

        Queries Wayback to see if the content is there yet.

        e.g.
        http://192.168.99.100:8080/wayback/xmlquery.jsp?type=urlquery&url=https://www.gov.uk/government/uploads/system/uploads/attachment_data/file/497662/accidents-involving-illegal-alcohol-levels-2014.pdf

        <wayback>
        <request>
            <startdate>19960101000000</startdate>
            <resultstype>resultstypecapture</resultstype>
            <type>urlquery</type>
            <enddate>20160204115837</enddate>
            <firstreturned>0</firstreturned>
            <url>uk,gov)/government/uploads/system/uploads/attachment_data/file/497662/accidents-involving-illegal-alcohol-levels-2014.pdf
    </url>
            <resultsrequested>10000</resultsrequested>
            <resultstype>resultstypecapture</resultstype>
        </request>
        <results>
            <result>
                <compressedoffset>2563</compressedoffset>
                <mimetype>application/pdf</mimetype>
                <redirecturl>-</redirecturl>
                <file>BL-20160204113809800-00000-33~d39c9051c787~8443.warc.gz
    </file>
                <urlkey>uk,gov)/government/uploads/system/uploads/attachment_data/file/497662/accidents-involving-illegal-alcohol-levels-2014.pdf
    </urlkey>
                <digest>JK2AKXS4YFVNOTPS7Q6H2Q42WQ3PNXZK</digest>
                <httpresponsecode>200</httpresponsecode>
                <robotflags>-</robotflags>
                <url>https://www.gov.uk/government/uploads/system/uploads/attachment_data/file/497662/accidents-involving-illegal-alcohol-levels-2014.pdf
    </url>
                <capturedate>20160204113813</capturedate>
            </result>
        </results>
    </wayback>
    """
    task_namespace = 'wb'
    url = luigi.Parameter()
    ts = luigi.Parameter()
    check_available = luigi.BoolParameter(default=False)

    resources = { 'qa-wayback': 1 }

    def complete(self):
        try:
            # Check if the item+timestamp is known:
            known = self.check_if_known()
            if self.check_available:
                if known:
                    # Check if the actual resource is available:
                    available = self.check_if_available()
                    return available
                # Otherwise:
                return False
            else:
                return known
        except Exception as e:
            logger.error("%s [%s %s]" % (str(e), self.url, self.ts))
            logger.exception(e)
        # Otherwise:
        return False

    def check_if_known(self):
        """
        Checks if a resource with a particular timestamp is available in the index:
        :return:
        """
        wburl = '%s/xmlquery.jsp?type=urlquery&url=%s' % (WAYBACK_PREFIX, quote(self.url))
        logger.debug("Checking availability %s" % wburl)
        r = requests.get(wburl)
        logger.debug("Availability response: %d" % r.status_code)
        # Is it known, with a matching timestamp?
        if r.status_code == 200:
            dom = xml.dom.minidom.parseString(r.text)
            for de in dom.getElementsByTagName('capturedate'):
                if de.firstChild.nodeValue == self.ts:
                    # Excellent, it's been found:
                    return True
        else:
            return False

    def check_if_available(self):
        """
        Checks if the resource is actually accessible/downloadable.

        This is done separately, as using this alone may accidentally get an older version.
        :return:
        """
        wburl = '%s/%s/%s' % (WAYBACK_PREFIX, self.ts, self.url)
        logger.debug("Checking download %s" % wburl)
        r = requests.head(wburl)
        logger.debug("Download HEAD response: %d" % r.status_code)
        # Resource is present?
        if r.status_code == 200:
            return True
        else:
            return False


class ExtractDocumentAndPost(luigi.Task):
    """
    Hook into w3act, extract MD and resolve the associated target.

    Note that the output file uses only the URL to make a hash-based identifier grouped by host, so this will only
    process each URL it sees once. This makes sense as the current model does not allow different
    Documents at the same URL in W3ACT.
    """
    task_namespace = 'doc'
    job = luigi.Parameter()
    launch_id = luigi.Parameter()
    doc = luigi.DictParameter()
    source = luigi.Parameter()

    resources = { 'w3act': 1 }

    def requires(self):
        return {
            'targets': CrawlFeed('frequent'),
            'available' : AvailableInWayback(self.doc['document_url'], self.doc['wayback_timestamp'])
        }

    @staticmethod
    def document_target(host, hash):
        return luigi.LocalTarget('{}/documents/{}/{}'.format(LUIGI_STATE_FOLDER, host, hash))

    def output(self):
        hasher = hashlib.md5()
        hasher.update(self.doc['document_url'])
        return self.document_target(urlparse(self.doc['document_url']).hostname, hasher.hexdigest())

    def run(self):
        # If so, lookup Target and extract any additional metadata:
        targets = json.load(self.input()['targets'].open('r'))
        doc = DocumentMDEx(targets, self.doc.get_wrapped().copy(), self.source).mdex()
        # Documents may be rejected at this point:
        if doc is None:
            logger.critical("The document %s has been REJECTED!" % self.doc['document_url'])
            doc = self.doc.get_wrapped().copy()
            doc['status'] = 'REJECTED'
        else:
            # Inform W3ACT it's available:
            doc['status'] = 'ACCEPTED'
            logger.debug("Sending doc: %s" % doc)
            w = w3act(ACT_URL, ACT_USER, ACT_PASSWORD)
            r = w.post_document(doc)
            if r.status_code == 200:
                logger.info("Document POSTed to W3ACT: %s" % doc['document_url'])
            else:
                logger.error("Failed with %s %s\n%s" % (r.status_code, r.reason, r.text))
                raise Exception("Failed with %s %s\n%s" % (r.status_code, r.reason, r.text))

        # And write out to the status file
        with self.output().open('w') as out_file:
            out_file.write('{}'.format(json.dumps(doc, indent=4)))


class ExtractDocuments(luigi.Task):
    """
    Via required tasks, launched M-R job to process crawl logs.

    Then runs through output documents and attempts to post them to W3ACT.
    """
    task_namespace = 'doc'
    job = luigi.Parameter()
    launch_id = luigi.Parameter()

    def requires(self):
        return ScanLogFileForDocs(self.job, self.launch_id)

    def output(self):
        return luigi.LocalTarget('{}/documents/extracted-{}-{}'.format(LUIGI_STATE_FOLDER, self.job, self.launch_id))

    def run(self):
        # Loop over documents discovered, and attempt to post to W3ACT:
        with self.input().open('r') as in_file:
            for line in in_file:
                url, docjson = line.strip().split("\t", 1)
                doc = json.loads(docjson)
                yield ExtractDocumentAndPost(self.job, self.launch_id, doc, doc["source"])


class ScanForDocuments(ScanForOutputs):
    """
    This task scans the output folder for jobs and instances of those jobs, looking for crawls logs.
    """
    task_namespace = 'scan'
    scan_name = 'docs'

    def process_output(self, job, launch):
        yield ExtractDocuments(job, launch)


if __name__ == '__main__':
    luigi.run(['doc.ExtractDocuments', '--job', 'weekly', '--launch-id', '20170220090024', '--local-scheduler'])
    #luigi.run(['scan.ScanForDocuments', '--date-interval', '2017-02-10-2017-02-12', '--local-scheduler'])