import pickle
import logging
import subprocess
import concurrent.futures
from pathlib import Path

import luigi
from luigi.util import inherits

from recon.config import web_ports
from recon.masscan import ParseMasscanOutput


@inherits(ParseMasscanOutput)
class ThreadedNmap(luigi.Task):
    """ Run nmap against specific targets and ports gained from the ParseMasscanOutput Task.

    nmap commands are structured like the example below.

    nmap --open -sT -sC -T 4 -sV -Pn -p 43,25,21,53,22 -oA htb-targets-nmap-results/nmap.10.10.10.155-tcp 10.10.10.155

    The corresponding luigi command is shown below.

    PYTHONPATH=$(pwd) luigi --local-scheduler --module recon.nmap ThreadedNmap --target-file htb-targets --top-ports 5000

    Args:
        threads: number of threads for parallel nmap command execution
        rate: desired rate for transmitting packets (packets per second) *--* Required by upstream Task
        interface: use the named raw network interface, such as "eth0" *--* Required by upstream Task
        top_ports: Scan top N most popular ports *--* Required by upstream Task
        ports: specifies the port(s) to be scanned *--* Required by upstream Task
        target_file: specifies the file on disk containing a list of ips or domains *--* Required by upstream Task
    """

    threads = luigi.Parameter(default=10)

    def requires(self):
        """ ThreadedNmap depends on ParseMasscanOutput to run.

        TargetList expects target_file as a parameter.
        Masscan expects rate, target_file, interface, and either ports or top_ports as parameters.

        Returns:
            luigi.Task - ParseMasscanOutput
        """
        args = {
            "rate": self.rate,
            "target_file": self.target_file,
            "top_ports": self.top_ports,
            "interface": self.interface,
            "ports": self.ports,
        }
        return ParseMasscanOutput(**args)

    def output(self):
        """ Returns the target output for this task.

        Naming convention for the output folder is TARGET_FILE-nmap-results.

        The output folder will be populated with all of the output files generated by
        any nmap commands run.  Because the nmap command uses -oA, there will be three
        files per target scanned: .xml, .nmap, .gnmap.

        Returns:
            luigi.local_target.LocalTarget
        """
        return luigi.LocalTarget(f"{self.target_file}-nmap-results")

    def run(self):
        """ Parses pickled target info dictionary and runs targeted nmap scans against only open ports. """
        try:
            self.threads = abs(int(self.threads))
        except TypeError:
            return logging.error("The value supplied to --threads must be a non-negative integer.")

        ip_dict = pickle.load(open(self.input().path, "rb"))

        nmap_command = [  # placeholders will be overwritten with appropriate info in loop below
            "nmap",
            "--open",
            "PLACEHOLDER-IDX-2" "-n",
            "-sC",
            "-T",
            "4",
            "-sV",
            "-Pn",
            "-p",
            "PLACEHOLDER-IDX-10",
            "-oA",
        ]

        commands = list()

        """
        ip_dict structure
        {
            "IP_ADDRESS":
                {'udp': {"161", "5000", ... },
                ...
                i.e. {protocol: set(ports) }
        }
        """
        for target, protocol_dict in ip_dict.items():
            for protocol, ports in protocol_dict.items():
                non_web_ports = ",".join(ports.difference(web_ports))

                if not non_web_ports:
                    continue

                tmp_cmd = nmap_command[:]
                tmp_cmd[2] = "-sT" if protocol == "tcp" else "-sU"

                # arg to -oA, will drop into subdir off curdir
                tmp_cmd[9] = non_web_ports
                tmp_cmd.append(f"{self.output().path}/nmap.{target}-{protocol}")

                tmp_cmd.append(target)  # target as final arg to nmap

                commands.append(tmp_cmd)

        # basically mkdir -p, won't error out if already there
        Path(self.output().path).mkdir(parents=True, exist_ok=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            executor.map(subprocess.run, commands)


@inherits(ThreadedNmap)
class Searchsploit(luigi.Task):
    """ Run searchcploit against each nmap*.xml file in the TARGET-nmap-results directory and write results to disk.

    searchsploit commands are structured like the example below.

    searchsploit --nmap htb-targets-nmap-results/nmap.10.10.10.155-tcp.xml

    The corresponding luigi command is shown below.

    PYTHONPATH=$(pwd) luigi --local-scheduler --module recon.nmap Searchsploit --target-file htb-targets --top-ports 5000

    Args:
        threads: number of threads for parallel nmap command execution *--* Required by upstream Task
        rate: desired rate for transmitting packets (packets per second) *--* Required by upstream Task
        interface: use the named raw network interface, such as "eth0" *--* Required by upstream Task
        top_ports: Scan top N most popular ports *--* Required by upstream Task
        ports: specifies the port(s) to be scanned *--* Required by upstream Task
        target_file: specifies the file on disk containing a list of ips or domains *--* Required by upstream Task
    """

    def requires(self):
        """ Searchsploit depends on ThreadedNmap to run.

        TargetList expects target_file as a parameter.
        Masscan expects rate, target_file, interface, and either ports or top_ports as parameters.
        ThreadedNmap expects threads

        Returns:
            luigi.Task - ThreadedNmap
        """
        args = {
            "rate": self.rate,
            "ports": self.ports,
            "threads": self.threads,
            "top_ports": self.top_ports,
            "interface": self.interface,
            "target_file": self.target_file,
        }
        return ThreadedNmap(**args)

    def output(self):
        """ Returns the target output for this task.

        Naming convention for the output folder is TARGET_FILE-searchsploit-results.

        The output folder will be populated with all of the output files generated by
        any searchsploit commands run.

        Returns:
            luigi.local_target.LocalTarget
        """
        return luigi.LocalTarget(f"{self.target_file}-searchsploit-results")

    def run(self):
        """ Grabs the xml files created by ThreadedNmap and runs searchsploit --nmap on each one, saving the output. """
        for entry in Path(self.input().path).glob("nmap*.xml"):
            proc = subprocess.run(["searchsploit", "--nmap", str(entry)], stderr=subprocess.PIPE)
            if proc.stderr:
                Path(self.output().path).mkdir(parents=True, exist_ok=True)

                # change  wall-searchsploit-results/nmap.10.10.10.157-tcp to 10.10.10.157
                target = entry.stem.replace("nmap.", "").replace("-tcp", "").replace("-udp", "")

                Path(
                    f"{self.output().path}/searchsploit.{target}-{entry.stem[-3:]}.txt"
                ).write_bytes(proc.stderr)
