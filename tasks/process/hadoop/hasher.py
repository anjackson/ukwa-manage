import os
import json
import datetime
import logging
import subprocess
import luigi
import luigi.contrib.hdfs
import luigi.contrib.hadoop_jar

logger = logging.getLogger('luigi-interface')

LUIGI_STATE_FOLDER = os.environ.get('LUIGI_STATE_FOLDER','./state')


def state_file(date, tag, suffix, on_hdfs=False):
    path = os.path.join( LUIGI_STATE_FOLDER,
                         date.strftime("%Y-%m"),
                         tag,
                         '%s-%s' % (date.strftime("%Y-%m-%d"), suffix))
    if on_hdfs:
        return luigi.contrib.hdfs.HdfsTarget(path=path)
    else:
        return luigi.LocalTarget(path=path)


class ListAllFilesOnHDFS(luigi.Task):
    """
    This task lists all files on HDFS. As this can be a very large list, it avoids reading it all into memory. It
    parses each line, and creates a JSON item for each, outputting the result in
    [JSON Lines format](http://jsonlines.org/).

    It set up to run once a day, as input to downstream reporting or analysis processes.
    """
    date = luigi.DateParameter(default=datetime.date.today())

    def output(self):
        return state_file(self.date,'hdfs','all-files-list.jsonl')

    def run(self):
        command = luigi.contrib.hdfs.load_hadoop_cmd()
        command += ['fs', '-lsr', '/']
        with self.output().open('w') as f:
            process = subprocess.Popen(command, stdout=subprocess.PIPE)
            for line in iter(process.stdout.readline, ''):
                if "lsr: DEPRECATED: Please use 'ls -R' instead." in line:
                    logger.warning(line)
                else:
                    permissions, number_of_replicas, userid, groupid, filesize, modification_date, modification_time, filename = line.split()
                    timestamp = datetime.datetime.strptime('%s %s' % (modification_date, modification_time), '%Y-%m-%d %H:%M')
                    info = {
                        'permissions' : permissions,
                        'number_of_replicas': number_of_replicas,
                        'userid': userid,
                        'groupid': groupid,
                        'filesize': filesize,
                        'modified_at': timestamp.isoformat(),
                        'filename': filename
                    }
                    f.write(json.dumps(info)+'\n')


class ListWebArchiveFilesOnHDFS(luigi.Task):
    date = luigi.DateParameter(default=datetime.date.today())

    def requires(self):
        return ListAllFilesOnHDFS(self.date)

    def output(self):
        return state_file(self.date, 'hdfs', 'warc-files-list.jsonl')

    def run(self):
        for line in self.input().open('r'):
            item = json.loads(line.strip())
            if item['filename'].endswith('.warc.gz') or item['filename'].endswith('.arc.gz'):
                print(item)


class GenerateWarcHashes(luigi.contrib.hadoop_jar.HadoopJarJobTask):
    """
    Generates the SHA-512 hashes for the WARCs directly on HDFS.

    Parameters:
        input_file: A local file that contains the list of WARC files to process
    """
    input_file = luigi.Parameter()

    def output(self):
        out_name = "%s-sha512.tsv" % os.path.splitext(self.input_file)[0]
        return luigi.contrib.hdfs.HdfsTarget(out_name, format=luigi.contrib.hdfs.Plain)

    #def requires(self):
    #    return tasks.report.crawl_summary.GenerateWarcList(self.input_file)

    def jar(self):
        return "../jars/warc-hadoop-recordreaders-2.2.0-BETA-7-SNAPSHOT-job.jar"

    def main(self):
        return "uk.bl.wa.hadoop.mapreduce.hash.HdfsFileHasher"

    def args(self):
        return [self.input_file, self.output()]


if __name__ == '__main__':
    luigi.run(['ListWebArchiveFilesOnHDFS', '--local-scheduler'])
#    luigi.run(['GenerateWarcHashes', 'daily-warcs-test.txt'])
