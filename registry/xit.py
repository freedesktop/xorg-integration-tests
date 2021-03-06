#!/usr/bin/env python
#
# This program is a bug registry to compare test results between different
# runs of a test suite. It's prime purpose is to track known failures and
# alarm if a test does not yield the result expected.
#
# Input files are either xml files created by this registry, or JUnit XML
# files obtained from the test suite if --gtest_output="xml:filename.xml" is
# set.
#
# Default input/output for registry files is stdin/stdout unless -f is
# given. If -f is given, that file is both input and output file and is
# modified in-place.
#
# Usage examples:
#       Create test registry from test results:
#       xit-bug-registry create some-results.xml > registry.xml
#       or
#       xit-bug-registry -f registry.xml create some-results.xml
#
#       Verify test results against existing registry:
#       xit-bug-registry -f registry.xml verify some-results.xml
#
#       Merge a new test registry into an existing one, adding new test
#       cases from second-registry but leaving the existing ones untouched:
#       xit-bug-registry -f registry.xml merge first-registry.xml second-registry.xml
#
#       Print info about a test:
#       xit-bug-registry info MyTestSuite TestName < registry.xml
#
#       Add information about a specific test case:
#       xit-bug-registry -f registry.xml edit MyTestSuite TestName add-bug http://bugs.freedesktop.org/1234556
#       xit-bug-registry -f registry.xml edit MyTestSuite TestName rm-bug http://bugs.freedesktop.org/1234556
#       xit-bug-registry -f registry.xml edit MyTestSuite TestName add-rpm xorg-x11-server-1.23-4.fc18.rpm
#       xit-bug-registry -f registry.xml edit MyTestSuite TestName rm-rpm xorg-x11-server-1.23-4.fc18.rpm
#       xit-bug-registry -f registry.xml edit MyTestSuite TestName add-commit 12354534deadbeef
#       xit-bug-registry -f registry.xml edit MyTestSuite TestName rm-commit 12354534deadbeef
#

import sys
import os
import time
import argparse
from lxml import objectify
import lxml.etree
import shutil
import subprocess
import logging

DEFAULT_MODULES = ["xorg-x11-server-Xorg",
                   "xorg-x11-drv-evdev",
                   "xorg-x11-drv-synaptics",
                   "xorg-x11-drv-wacom",
                   "xorg-x11-drv-mouse",
                   "xorg-x11-drv-keyboard"]

class Colors:
        DEFAULT = 0
        RED = 1
        GREEN = 2
        BLUE = 3

XMLNS = "http://www.x.org/xorg-integration-testing"
def xmlns_tag(tag, ns=XMLNS):
    """Helper function to create a namespaced tag from the tag. Needed for
       iterchildren() which only takes fully qualified tags, apparently"""
    return "{" + ns + "}" + tag

def str2bool(val):
    if val == True or val == False:
        return val
    if val in ["True", "true", "1"]:
        return True
    elif val in ["False", "false", "0"]:
        return False
    else:
        raise ValueError

class termcolors:
    DEFAULT = '\033[0m'
    RED = '\033[1;31m'
    GREEN = '\033[1;32m'
    BLUE = '\033[1;34m'

    @classmethod
    def color(self, color):
        colors = { Colors.DEFAULT : termcolors.DEFAULT,
                   Colors.RED : termcolors.RED,
                   Colors.GREEN : termcolors.GREEN,
                   Colors.BLUE : termcolors.BLUE }
        return colors[color]

    @classmethod
    def disable(self):
        self.DEFAULT = ''
        self.RED = ''
        self.GREEN = ''
        self.BLUE = ''

class XITTestRegistry:
    """Central class keeping a set of test cases and their results"""

    def __init__(self, name="", test_cases = [], path="stdin"):
        """Initialise with a registry name and a list of """
        self.tests = self._from_list(test_cases)
        self.name = name
        self.date = time.localtime()
        self.moduleversions = []
        self.path = path

    @classmethod
    def fromXML(self, filename):
        """Generate and returns a list of XITTestRegistry from the xml file at filename"""
        registries = objectify.parse(filename).getroot()
        regs = []
        for registry in registries.iterchildren(tag=xmlns_tag("registry")):
            reg = XITTestRegistry(name=registry.attrib["name"], path=filename)

            for meta in registry.iterchildren(tag=xmlns_tag("meta")):
                date = meta.find(xmlns_tag("date"))
                if date != None:
                    reg.date = time.strptime(date.text, "%Y-%m-%d")

                for modversion in meta.iterchildren(tag=xmlns_tag("moduleversion")):
                    try:
                        type = modversion.attrib["type"]
                    except KeyError:
                        type = "git"
                    try:
                        repo = modversion.attrib["repo"]
                    except KeyError:
                        repo = None
                    mv = XITModuleVersion(modversion.attrib["name"], modversion.text, type, repo)
                    reg.moduleversions.append(mv)

            for suite in registry.iterchildren(tag=xmlns_tag("testsuite")):
                for testcase in suite.iterchildren(tag=xmlns_tag("testcase")):
                    tcase = XITTest(suite.attrib["name"],
                                    testcase.attrib["name"],
                                    str2bool(testcase.attrib["success"]))
                    for bug in testcase.iterchildren(tag=xmlns_tag("bug")):
                        try:
                            type = bug.attrib["type"]
                        except KeyError:
                            type = "bugzilla"
                        tcase.addBug(XITBug(type, bug.text))

                    for info in testcase.iterchildren(tag=xmlns_tag("testinfo")):
                        try:
                            type = info.attrib["type"]
                        except KeyError:
                            type = "text"
                        tcase.addInfo(XITInfo.createFromType(type, info.text))

                    for fix in testcase.iterchildren(tag=xmlns_tag("fix")):
                        type = fix.attrib["type"]
                        tcase.addFix(XITFix.createFromType(type, fix.text, fix.attrib))

                    reg.addTest(tcase)
            regs.append(reg)
        return regs

    def toXML(self, others=[]):
        """Generate XML output from this registry and return it. If others
           is a list, the resulting XML file contains all of the registries
           provided including self. Otherwise, only self is written out."""
        NSMAP = { "xit" : "http://www.x.org/xorg-integration-testing" }
        E = objectify.ElementMaker(annotate = False,
                                   namespace = NSMAP['xit'],
                                   nsmap = NSMAP)

        xit_registries = E.registries()

        if len(others) == 0:
            others = [self] # yikes

        for r in others:
            xit_registry = E.registry()
            xit_registry.set("name", r.name)
            xit_registries.append(xit_registry)

            xit_meta = E.meta()
            xit_registry.append(xit_meta)

            xit_date = E.date(time.strftime("%Y-%m-%d", r.date))
            xit_meta.append(xit_date)

            for modversion in sorted(r.moduleversions):
                xit_modversion = E.moduleversion(modversion.version)
                xit_modversion.set("name", modversion.module)
                xit_modversion.set("type", modversion.type)
                if modversion.repo:
                    xit_modversion.set("repo", modversion.repo)

                xit_meta.append(xit_modversion)

            for suite_name, suite in sorted(r.tests.iteritems()):
                xit_suite = E.testsuite()
                xit_suite.set("name", suite_name)
                for name, test in sorted(suite.iteritems()):
                    xit_testcase = E.testcase()
                    xit_testcase.set("name", test.name)
                    xit_testcase.set("success", str(test.status).lower())

                    for bug in test.getBugs():
                        xit_bug = E.bug(bug.url)
                        xit_bug.set("type", bug.type)
                        xit_testcase.append(xit_bug)

                    for fix in test.getFixes():
                        xit_fix = E.fix(fix.text)
                        xit_fix.set("type", fix.type);
                        for arg, value in fix.extra_args.iteritems():
                            xit_fix.set(arg, value)
                        xit_testcase.append(xit_fix)

                    for info in test.getInfo():
                        xit_info = E.testinfo(info.text)
                        xit_info.set("type", info.type)
                        xit_testcase.append(xit_info)

                    xit_suite.append(xit_testcase)
                xit_registry.append(xit_suite)

        lxml.etree.cleanup_namespaces(xit_registries)
        return lxml.etree.tostring(xit_registries, pretty_print=True)

    def _from_list(self, list_in):
        """Convert a list of test cases into the internally used dict[suite name][test case name] = XITestCase"""
        l = {}
        for elem in list_in:
            if not l.has_key(elem.suite):
                l[elem.suite] = {}
            l[elem.suite][elem.name] = elem
        return l

    def listTestNames(self):
        """Return a list of tuples (suite name, test name, test status) for all tests """
        tests = []
        for suite_name, suite in sorted(self.tests.iteritems()):
            for test_name, test in sorted(suite.iteritems()):
                tests.append((suite_name, test_name, test.status))
        return tests

    def getTest(self, testsuite, testcase):
        """Return the test named by suite name and test case name, or None if none exists"""
        try:
            return self.tests[testsuite][testcase]
        except KeyError:
            return None

    def addTest(self, test):
        """Add a test to this registry"""
        if not self.tests.has_key(test.suite):
            self.tests[test.suite] = {}
        self.tests[test.suite][test.name] = test

    def __cmp__(self, other):
        return cmp(self.name, other)

class XITTestCase:
    """Represents one single test case, comprised of test suite name and test case name.
       A test has a status (true on success, false on failure).
       This class is merely a common parent class for XIT and JUnit test results"""

    def __init__(self, suite, name, status = True):
        self.suite = suite
        self.name = name
        self.status = str2bool(status)

    @property
    def status(self):
        return self.status

    @status.setter
    def status(self, val):
        if type(val) == str:
            self.status = str2bool(val)
        elif type(val) == bool:
            self.status = val
        else:
            raise ValueError

    def __cmp__(self, other):
        if other == None:
            return 1
        rc = cmp(self.suite, other.suite)
        if rc == 0:
            rc = cmp(self.name, other.name)
        return rc

class XITTest(XITTestCase):
    """Represents one single XIT test case, comprised of test suite name and test case name.
       A test has a status (true on success, false on failure) and may have zero or more
       bugs, fix or general info tags attached"""
    def __init__(self, suite, name, success):
        XITTestCase.__init__(self, suite, name, success)
        self._bugs = [] # XITBug
        self._info = [] # XITInfo
        self._fixes = [] # XITFix

    def addBug(self, bug):
        """Add a reference to a known bug to this test case"""
        if not bug in self._bugs:
            self._bugs.append(bug)

    def removeBug(self, bug):
        """Remove a reference to a known bug to this test case"""
        try:
            self._bugs.remove(bug)
        except ValueError:
            pass

    def addInfo(self, info):
        """Add some general information about this test case"""
        if not info in self._info:
            self._info.append(info)

    def removeInfo(self, info):
        """Add some general information about this test case"""
        try:
            self._info.remove(info)
        except ValueError:
            pass

    def addFix(self, fix):
        """Add fix information about this test case"""
        if not fix in self._fixes:
            self._fixes.append(fix)

    def removeFix(self, fix):
        """Add fix information about this test case"""
        try:
            self._fixes.remove(fix)
        except ValueError:
            pass

    def getBugs(self):
        """Return a list of all bug registered with this test case"""
        return sorted(self._bugs)

    def getInfo(self):
        """Return a list of all general info pieces registered with this test case"""
        return sorted(self._info)

    def getFixes(self):
        """Return a list of all fixes registered with this test case"""
        return sorted(self._fixes)

    def __str__(self):
        s = self.suite + " " + self.name + ":\tExpected result: " + ("Success" if self.status else "Failure") + "\n"
        if len(self._info):
            s += "Extra info:\n"
            i = 0
            for info in self._info:
                s += str(i) + ": " + str(info) + "\n"
                i += 1
        if len(self._bugs):
            s += "Known bugs: \n"
            i = 0
            for bug in self._bugs:
                s += str(i) + ": " + str(bug) + "\n"
                i += 1
        if len(self._fixes):
            s += "Known fixes: \n"
            i = 0
            for fix in self._fixes:
                s += str(i) + ": " + str(fix) + "\n"
                i += 1
        return s


class XITBug:
    """Represents a known bug related to a specific test case. Usually, this
       will be a link to a bugzilla database"""

    default_type = "bugzilla"

    def __init__(self, bug_type=default_type, url=""):
        self.type = bug_type
        self.url = url

    def __str__(self):
        return self.url

    def __eq__(self, other):
        return self.type == other.type and self.url == other.url

    def __cmp__(self, other):
        rc = cmp(self.type, other.type)
        if rc == 0:
            rc = cmp(self.url, other.url)
        return rc

class XITFix:
    """Represents a fix known to alter the outcome of a testcase"""
    def __init__(self):
        self.type = None
        self.text = None
        self.extra_args = {}

    def __str__(self):
        return self.text

    def __eq__(self, other):
        return self.type == other.type and self.text == other.text

    def __cmp__(self, other):
        rc = cmp(self.type, other.type)
        if rc == 0:
            rc = cmp(self.text, other.text)
        return rc

    @classmethod
    def createFromType(self, type, text, extra_args = {}):
        if type == "git":
            return XITFixGit(text, extra_args)
        elif type == "rpm":
            return XITFixRPM(text, extra_args)
        raise ValueError

class XITFixGit(XITFix):
    """Represents a git commit known to affect the outcome of a testcase"""
    def __init__(self, sha1, extra_args = {}):
        self.type = "git"
        self.text = sha1
        self.extra_args = extra_args

    @property
    def sha1(self):
        return self.text

    def __str__(self):
        return self.sha1

class XITFixRPM(XITFix):
    """Represents a rpm package known to affect the outcome of a testcase"""
    def __init__(self, rpm, extra_args = {}):
        self.type = "rpm"
        self.text = rpm
        self.extra_args = extra_args

    @property
    def rpm(self):
        return self.text

    def __str__(self):
        return self.sha1

class XITInfo:
    """Represents a piece of generic information about a bug."""
    def __init__(self):
        self.type = None
        self.text = None

    @classmethod
    def createFromType(self, type, text, params={}):
        """Create a XITInfo of the right type and return it"""
        if type == "text":
            return XITInfoText(text)
        elif type == "url":
            return XITInfoURL(text)
        raise ValueError

    def __cmp__(self, other):
        rc = cmp(self.type, other.type)
        if rc == 0:
            rc = cmp(self.text, other.text)
        return rc

class XITInfoText(XITInfo):
    """Represents a piece of generic textual information about a bug."""
    def __init__(self, text):
        self.text = text
        self.type = "text"

    def __str__(self):
        return self.text

class XITInfoURL(XITInfo):
    """Represents a piece of generic information about a bug in the form of a URL."""
    def __init__(self, url):
        self.text = url
        self.type = "url"

    @property
    def url(self):
        return self.text

    def __str__(self):
        return self.url

class XITModuleVersion:
    """Represents a module version of a particular component."""
    def __init__(self, module, version, type = "git", repo=None):
        self.module = module
        self.version = version
        self.repo = repo
        self.type = type

    def __str__(self):
        return self.module + ": " + self.version

    def __cmp__(self, other):
        rc = cmp(self.module, other.module)
        if rc == 0:
            rc = cmp(self.type, other.type)
            if rc == 0:
                rc = cmp(self.version, other.version)
        return rc

    def compareSystemVersion(self):
        if self.type != "rpm":
            raise NotImplementedError("Only supported for type rpm")

        command = "rpm -q " + self.module
        p = subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, "")
        version = p.stdout.readline().strip()

        return self.version == version, version

    @classmethod
    def getSystemVersion(self, module, type ="rpm"):
        if type != "rpm":
            raise NotImplementedError()

        command = "rpm -q " + module
        p = subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)
        p.wait()
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, "")
        version = p.stdout.readline().strip()

        return XITModuleVersion(module, version, type)


class JUnitTestResult(XITTestCase):
    """Represents a JUnit test result in XML format, generated by --gtest_output="xml:filename.xml" """
    def __init__(self, suite, name):
        XITTestCase.__init__(self, suite, name, True)
        self.failures = []

    def addFailure(self, failure):
        self.failures.append(failure)
        self.status = False

    def __str__(self):
        s = self.suite + "." + self.name + ":\t"
        if len(self.failures) == 0:
            s += "success";
        else:
            s += "failure"
            s += "\nFailures:\n"
            for failure in self.failures:
                s += str(failure) + "\n"
        return s

    @classmethod
    def fromXML(self, filename):
        """Return a list of JUnitTestResults based on the xml file given"""
        testsuites = objectify.parse(filename).getroot()
        results = []
        for testsuite in testsuites.iterchildren(tag="testsuite"):
            for testcase in testsuite.iterchildren(tag="testcase"):
                result = JUnitTestResult(testsuite.attrib["name"], testcase.attrib["name"])
                results.append(result)

                for failure in testcase.iterchildren(tag="failure"):
                    result.addFailure(JUnitTestFailure(failure.attrib["message"]))
        return results

class JUnitTestFailure:
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class XITCLIPrinter:
    def __init__(self):
        pass

    def separator(self, text):
        print ":" * 20 + " {:<30} ".format(text) + ":" * 58

    def print_values(self, headers, values, colors = None, section=None):
        if len(headers) != len(values[0]):
            logging.error("Mismatched header/values tuples. Refusing to print")
            return

        if section != None:
            self.separator(section)

        cw = [] # column width
        separators = []
        for header in headers:
            cw.append(len(header))
            separators.append("-" * len(header))

        for val in values:
            for i in range(len(val)):
                cw[i] = max(cw[i], len(str(val[i])))

        format_str = ""
        for idx, w in zip(range(len(cw)), cw):
            format_str += "{%d:<%d}" % (idx, w + 1)

        print format_str.format(*headers)
        print format_str.format(*separators)

        if not colors:
            colors = [Colors.DEFAULT] * len(values)

        for color, val in zip(colors, values):
            print termcolors.color(color) + format_str.format(*val) + termcolors.DEFAULT


class XITPrinterPicker(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if values == "html":
            printer = XITHTMLPrinter()
        else:
            printer = XITCLIPrinter()

        setattr(namespace, "printer", printer)


class XITHTMLPrinter:
    def __init__(self):
        self.print_html_header()

    def __del__(self):
        self.print_html_footer()

    def print_html_header(self):
        print """<html> <head>
        <style>
        body{font-family:sans-serif;}
        .values {border-collapse:collapse;font-size:80%;}
        .values td, th {padding:0.5em 1em; border:1px solid #ccc; text-align:left;} values {background-color:#eee;text-align:left; font-size:80%;}
        .red { background-color:#ee0000; text: #000000; }
        </style>
        </head><body>
              """

    def print_html_footer(self):
        print """</body></html>
              """

    def print_table_header(self):
        print """<table class=\"values\">"""

    def print_table_footer(self):
        print """</table>"""

    def print_values(self, headers, values, colors = None, section = None):
        if section:
            print "<h2>%s</h2>" % section
        self.print_table_header()

        format_str = "<tr>"
        format_str += "<th>{}</th>" * len(headers)
        format_str += "</tr>"
        print format_str.format(*headers)

        if not colors:
            colors = [Colors.DEFAULT] * len(values)

        for color, val in zip(colors, values):
            if color == Colors.RED:
                format_str = "<tr class=\"red\">"
            else:
                format_str = "<tr>"

            format_str += "<td>{}</td>" * len(val)
            format_str += "</tr>"
            print format_str.format(*val)

        self.print_table_footer()

class XITTestRegistryCLI:
    """Command-line interface to the registry"""

    def list_tests(self, args):
        """List all tests, showing test name and expected status"""
        registries = self.load_registries(args)
        for r in registries:
            all_tests = r.listTestNames()

            headers = ("TestSuite", "TestCase", "Success")
            values = [(suite, test, str(status)) for suite, test, status in all_tests]
            colors = [Colors.RED if not status else Colors.DEFAULT for (suite, test, status) in all_tests]

            args.printer.print_values(headers, values, colors = colors, section = r.name)

    def show_test_info(self, args):
        """Show all information about a given XIT registry test case"""
        registry = self.load_one_registry(args)
        test = registry.getTest(args.testsuite, args.testcase)
        if test != None:
            print str(test)
        else:
            logging.error("Unable to find test %s.%s\n", args.testsuite, args.testcase)

    def verify_one_result(self, test, result):
        """Verify the test result given against the registry. Prints a status
           message comprising full test name, expected outcome, actual outcome
           and a grep-able bit to indicate which way the outcome differs"""


        color = Colors.DEFAULT

        if test != None and result != None:
            suite = test.suite
            name = test.name
            status = str(result.status).lower()
            expected_status = str(test.status).lower()
            if str(test.status).lower() != str(result.status).lower():
                status_match = "XX"
                if result.status == True:
                    color = Colors.GREEN
                else:
                    color = Colors.RED
            else:
                status_match = "++" if test.status else "--"
        elif test != None and result == None:
            suite = test.suite
            name = test.name
            status_match = "??"
            expected_status = str(test.status).lower()
            status = ""
            color = Colors.BLUE
        else:
            suite = result.suite
            name = result.name
            status = str(result.status).lower()
            expected_status = ""
            status_match = "??"
            color = Colors.BLUE

        return (color, (status_match, suite, name, status, expected_status))


    def check_installed_rpms(self, registry):
        self.print_modversions("Module name", registry.name, "System-installed", False)
        self.print_modversions("-----------", "-------------", "----------------", False)
        for moduleversion in registry.moduleversions:
            if moduleversion.type != "rpm":
                continue
            rc, version = moduleversion.compareSystemVersion()
            self.print_modversions(moduleversion.module, moduleversion.version, version)


    def verify_results(self, args):
        """Verify a JUnit test result against the XIT test registry.
           If a registry name is given, compare to that. If none is given
           and there is more than one registry, search for one named the
           same as the results file. Otherwise, bail out."""
        registry = None
        registries = self.load_registries(args)
        if not args.regname:
            regname = os.path.basename(args.results).split(".xml")[0]
            for r in registries:
                if r.name == regname:
                    registry = r
                    break
            if registry == None:
                logging.error("Failed to match %s with a registry." % args.results)
                sys.exit(1)
        else:
            registry = registries[0]

        results = JUnitTestResult.fromXML(args.results)

        if args.check_all:
            self.check_installed_rpms(registry)

        headers = ("Code", "TestSuite", "TestCase", "Result", "Expected")
        values = []
        colors = []
        for result in sorted(results):
            c, v = self.verify_one_result(registry.getTest(result.suite, result.name), result)
            colors.append(c)
            values.append(v)

        args.printer.print_values(headers, values, colors = colors, section = registry.name)

    def print_modversions(self, name, v1, v2, use_colors=True):
        if use_colors and v1 != v2:
            color = termcolors.RED
        else:
            color = termcolors.DEFAULT
        format_str = "{0:<30} {1:<50} {2:<50}"
        print color + format_str.format(name, v1, v2) + termcolors.DEFAULT

    def get_meta_comparison(self, reg1, reg2):
        modules = [ r.module for r in reg1.moduleversions]
        modules.extend(r.module for r in reg2.moduleversions if r.name not in modules)

        ret = []
        for m in modules:
            r1 = "".join([ mr1.version for mr1 in reg1.moduleversions if mr1.module == m ])
            r2 = "".join([ mr2.version for mr2 in reg2.moduleversions if mr2.module == m ])
            ret.append((m, r1, r2))

        return ret

    def compare_registries(self, args):
        regs1 = XITTestRegistry.fromXML(args.reg1)
        regs2 = XITTestRegistry.fromXML(args.reg2)

        # sort them so searching is simpler
        regs1.sort()
        regs2.sort()

        regname = args.regname
        if regname != None:
            reg1 = self.find_reg(regname, regs1)
            if reg1 == None:
                logging.error("Failed to find '%s' in first registry" % regname)
                sys.exit(1)
            reg2 = self.find_reg(regname, regs2)
            if reg2 == None:
                logging.error("Failed to find '%s' in second registry" % regname)
                sys.exit(1)
            self.compare_registry(reg1, reg2, args.printer);
        else:
            failed_regs = []
            done_regs = []
            for r1 in regs1:
                r2 = self.find_reg(r1.name, regs2)
                if r2 == None:
                    failed_regs.append(r1.name)
                else:
                    self.compare_registry(r1, r2, args.printer)
                    done_regs.append(r1.name)
            for r2 in regs2:
                if r2.name in done_regs:
                    continue
                failed_regs.append(r2.name)

            for f in failed_regs:
                logging.error("Failed to compare '%s'" % f)


    def find_reg(self, name, reglist):
        r = [r for r in reglist if r.name == name]
        return r[0] if len(r) > 0 else None

    def compare_registry(self, reg1, reg2, printer):
        headers = ("Module name", reg1.path, reg2.path)
        values = self.get_meta_comparison(reg1, reg2)
        colors = [ Colors.RED if m[1] != m[2] else Colors.DEFAULT for m in values ]

        if len(values):
            printer.print_values(headers, values, colors = colors, section = reg1.name)

        headers = ("Code", "TestSuite", "TestCase", reg2.path, reg1.path)
        values = []
        colors = []
        for suite, test, status in sorted(reg1.listTestNames()):
            c, v = self.verify_one_result(reg1.getTest(suite, test), reg2.getTest(suite, test))
            colors.append(c)
            values.append(v)

        printer.print_values(headers, values, colors = colors)

    def create_registries(self, args):
        regs = []
        for r in args.results:
            regs.append(self.create_registry(r, args))

        r = self.open_new_registry(args)
        self.write_to_registry(r, regs[0].toXML(regs))
        self.sync_registry(args, r)

    def create_registry(self, path, args):
        """Create a new registry XML file based on the test cases in the JUnit file"""
        results_list = JUnitTestResult.fromXML(path)

        results_dict = {}
        for r in results_list:
            if not results_dict.has_key(r.suite):
                results_dict[r.suite] = []
            results_dict[r.suite].append(r)

        if args.name:
            reg_name = args.name[0]
        else:
            reg_name = os.path.basename(path).split(".xml")[0]

        registry = XITTestRegistry(reg_name);
        registry.date = time.localtime()

        if args.auto_modversion == ["rpm"]:
            for module in DEFAULT_MODULES:
                registry.moduleversions.append(XITModuleVersion.getSystemVersion(module, "rpm"))

        for suite in sorted(results_dict.keys()):
            for r in sorted(results_dict[suite]):
                testcase = XITTest(suite, r.name, str(r.status).lower())
                registry.addTest(testcase);

        return registry

    def merge_registries(self, args):
        """Merge two registries together"""
        if args.add:
            self.merge_add_registries(args)

    def merge_registry(self, reg1, reg2):
        tests2 = reg2.listTestNames()
        for suite, name, status in tests2:
            if reg1.getTest(suite, name) == None:
                reg1.addTest(reg2.getTest(suite, name))


    def merge_add_registries(self, args):
        """Merge registry args.reg2 into regs.arg1, leaving all existing information in reg1 untouched"""
        regs1 = XITTestRegistry.fromXML(args.reg1)
        regs2 = XITTestRegistry.fromXML(args.reg2)

        if args.regname != None:
            r1 = self.find_reg(args.regname, regs1)
            r2 = self.find_reg(args.regname, regs2)
            if r1 == None and r2 == None:
                logging.error("Invalid registrys name '%s'" % (args.regname))
                sys.exit(1)

            if r2 == None:
                pass # do nothing
            elif r1 == None:
                regs1.append(r2)
            else:
                self.merge_registry(r1, r2)
        else:
            succeeded = []
            for r1 in regs1:
                r2 = self.find_reg(r1.name, regs2)
                if r2 == None:
                    continue
                self.merge_registry(r1, r2)
                succeeded.append(r1.name)

            for r2 in regs2:
                if r2.name in succeeded:
                    continue
                regs1.append(r2)

        self.registry_from_string(args, r1.toXML(regs1))

    def add_bug(self, args):
        registry = self.load_one_registry(args)
        testcase = registry.getTest(args.testsuite, args.testcase)

        if testcase == None:
            logging.error("Invalid test name '%s %s'" % (args.testsuite, args.testcase))
            sys.exit(1)

        testcase.addBug(XITBug("bugzilla", args.url))
        self.registry_from_string(args, registry.toXML())

    def rm_bug(self, args):
        registry = self.load_one_registry(args)
        testcase = registry.getTest(args.testsuite, args.testcase)

        if testcase == None:
            logging.error("Invalid test name '%s %s'" % (args.testsuite, args.testcase))
            sys.exit(1)

        testcase.removeBug(XITBug("bugzilla", args.url))
        self.registry_from_string(args, registry.toXML())

    def add_fix(self, args, type, text, extra_args = {}):
        registry = self.load_one_registry(args)
        testcase = registry.getTest(args.testsuite, args.testcase)

        if testcase == None:
            logging.error("Invalid test name '%s %s'" % (args.testsuite, args.testcase))
            sys.exit(1)

        testcase.addFix(XITFix.createFromType(type, text, extra_args))
        self.registry_from_string(args, registry.toXML())

    def add_commit(self, args):
        extra_args = {}
        if args.repo:
            extra_args["repo"] = args.repo

        self.add_fix(args, "git", args.sha1, extra_args)

    def add_rpm(self, args):
        self.add_fix(args, "rpm", args.rpm)

    def rm_fix(self, args, type, text):
        registry = self.load_one_registry(args)
        testcase = registry.getTest(args.testsuite, args.testcase)

        if testcase == None:
            logging.error("Invalid test name '%s %s'" % (args.testsuite, args.testcase))
            sys.exit(1)

        testcase.removeFix(XITFix.createFromType(type, text))
        self.registry_from_string(args, registry.toXML())

    def rm_commit(self, args):
        self.rm_fix(args, "git", args.sha1)

    def rm_rpm(self, args):
        self.rm_fix(args, "rpm", args.rpm)

    def set_status(self, args):
        registry = self.load_one_registry(args)
        testcase = registry.getTest(args.testsuite, args.testcase)

        if testcase == None:
            logging.error("Invalid test name '%s %s'" % (args.testsuite, args.testcase))
            sys.exit(1)

        status = { "true" : True,
                   "false" : False,
                   "success" : True,
                   "failure" : False }

        try:
            testcase.status = status[args.status]
        except KeyError:
            logging.error("Invalid status code, allowed are %s" % ",".join(status.keys()))
            sys.exit(1)

        self.registry_from_string(args, registry.toXML())

    def set_date(self, args):
        registry = self.load_one_registry(args)
        date = args.date
        if date != None:
            date = time.strptime(date, "%Y-%m-%d")
        else:
            date = time.localtime()

        registry.date = date
        self.registry_from_string(args, registry.toXML())

    def set_modversion(self, args):
        registry = self.load_one_registry(args)
        name = args.name
        version = args.version
        type = args.type if args.type else "git"

        modversion = XITModuleVersion(name, version, type);
        if modversion not in registry.moduleversions:
            registry.moduleversions.append(modversion)

        self.registry_from_string(args, registry.toXML())

    def parse_cmdline(self, ):
        parser = argparse.ArgumentParser(description = "Parse XIT test results for "
                "known failures.\nRun XIT tests with the --gtest_output:xml and "
                "compare the test result with the registry of known test "
                "successes/failures.\n")
        parser.add_argument("-f", "--file", help="file containing XIT test registry, modified in-place (default: stdin/stdout) ", action="store", required=False)
        parser.add_argument("-r", "--regname", metavar="registry-name", default=None, help="Work on the named test registry (defaults to first if not given) ", action="store", required=False)
        parser.add_argument("--output-format", dest="printer", default=XITCLIPrinter(), required=False,
                            action=XITPrinterPicker, help="Pick output format (html, text ,default: text)")
        subparsers = parser.add_subparsers(title="Actions", help=None)

        list_subparser = subparsers.add_parser("list", help="List all test cases")
        list_subparser.set_defaults(func = self.list_tests)

        info_subparser = subparsers.add_parser("info", help="Print info about a specific test case")
        info_subparser.add_argument("testsuite", default=None, help="Test Suite name")
        info_subparser.add_argument("testcase", default=None, help="Test Case name")
        info_subparser.set_defaults(func = self.show_test_info)

        verify_subparser = subparsers.add_parser("verify", help="Compare JUnit test results against the registry")
        verify_subparser.add_argument("results", metavar="results.xml", help="The XML file containing test results")
        verify_subparser.add_argument("--check-all", default=False, action="store_true", help="Perform extra verification checks")
        verify_subparser.set_defaults(func = self.verify_results)

        compare_subparser = subparsers.add_parser("compare", help="Compare two test registries")
        compare_subparser.add_argument("reg1", metavar="registry1.xml", help="Registry file no 1")
        compare_subparser.add_argument("reg2", metavar="registry2.xml", help="Registry file no 2")
        compare_subparser.set_defaults(func = self.compare_registries)

        create_subparser = subparsers.add_parser("create", help="Create new XIT registry from JUnit test results")
        create_subparser.add_argument("results", metavar="results.xml", nargs="+", help="The XML file(s) containing test results")
        create_subparser.add_argument("--name", nargs=1, help="Human-readable name for registry (default: the filename)")
        create_subparser.add_argument("--auto-modversion", metavar="TYPE", nargs=1, help="Try to automatically get module versions for selected modules (default: rpm)")
        create_subparser.set_defaults(func = self.create_registries)

        merge_subparser = subparsers.add_parser("merge", help="Merge two registries together")
        merge_subparser.add_argument("reg1", metavar="registry1.xml", help="Registry file no 1")
        merge_subparser.add_argument("reg2", metavar="registry2.xml", help="Registry file no 2")
        merge_subparser.add_argument("--add", default=True, action="store_true", help="Merge new test cases from registry 2 into registry 1, leaving existing test cases unmodified")
        merge_subparser.set_defaults(func = self.merge_registries)

        edit_subparser = subparsers.add_parser("edit", help="Modify an entry in the registry")
        edit_subparser.add_argument("testsuite", default=None, help="Test Suite name")
        edit_subparser.add_argument("testcase", default=None, help="Test Case name")
        edit_subparsers = edit_subparser.add_subparsers(title="Actions", help=None)

        add_bug_subparser = edit_subparsers.add_parser("add-bug", help="Add link to bugzilla entry")
        add_bug_subparser.add_argument("url", help="URL to bugzilla entry")
        add_bug_subparser.set_defaults(func = self.add_bug)

        rm_bug_subparser = edit_subparsers.add_parser("rm-bug", help="Remove link to bugzilla entry")
        rm_bug_subparser.add_argument("url", help="URL to bugzilla entry")
        rm_bug_subparser.set_defaults(func = self.rm_bug)

        add_commit_subparser = edit_subparsers.add_parser("add-commit", help="Add git SHA1 of a commit that alters this test outcome")
        add_commit_subparser.add_argument("sha1", help="git commit SHA1")
        add_commit_subparser.add_argument("--repo", help="git repository")
        add_commit_subparser.set_defaults(func = self.add_commit)

        rm_commit_subparser = edit_subparsers.add_parser("rm-commit", help="Remove git SHA1 of a commit listed in this test")
        rm_commit_subparser.add_argument("sha1", help="git commit SHA1")
        rm_commit_subparser.set_defaults(func = self.rm_commit)

        add_rpm_subparser = edit_subparsers.add_parser("add-rpm", help="Add rpm package NVR that alters this test outcome")
        add_rpm_subparser.add_argument("rpm", help="rpm package NVR")
        add_rpm_subparser.set_defaults(func = self.add_rpm)

        rm_rpm_subparser = edit_subparsers.add_parser("rm-rpm", help="Remove rpm package NVR listed in this test")
        rm_rpm_subparser.add_argument("rpm", help="rpm package NVR")
        rm_rpm_subparser.set_defaults(func = self.rm_rpm)

        set_status_subparser = edit_subparsers.add_parser("set-status", help="Change the status of a bug")
        set_status_subparser.add_argument("status", help="expected test status (true/false)")
        set_status_subparser.set_defaults(func = self.set_status)

        meta_subparser = subparsers.add_parser("meta", help="Modify the meta information in the registry")
        meta_subparsers = meta_subparser.add_subparsers(title="Actions", help=None)

        set_date_subparser = meta_subparsers.add_parser("set-date", help="Update the date")
        set_date_subparser.add_argument("date", nargs="?", help="Updated date in the form of 2012-12-31 (default to today if missing)")
        set_date_subparser.set_defaults(func = self.set_date)

        modversion_subparser = meta_subparsers.add_parser("set-module-version", help="Change a module version")
        modversion_subparser.add_argument("name", help="Name of the module")
        modversion_subparser.add_argument("version", help="Version string, use 'none' to remove an entry")
        modversion_subparser.add_argument("--type", help="Version type, e.g. git, rpm");
        modversion_subparser.set_defaults(func = self.set_modversion)

        return parser

    def load_one_registry(self, args):
        """Load and return a single registry from args.file. If args.name is
           set, search for a named registry, otherwise return the first"""
        if args.file == None:
            logging.error("Reading from stdin")
            args.file = sys.stdin

        registries = self.load_registry_from_file(args.file)
        if args.regname != None:
            for r in registries:
                if r.name == args.regname:
                    return r
            logging.error("Failed to find requested registry %s." % args.regname)
            sys.exit(1)
        else:
            logging.warning("Multiple registries found, but no name given. Using first.")
            return registries[0]


    def load_registries(self, args):
        """Load and return all registries from args.file. If args.name is
           set, search for a named registry and return only that."""
        if args.file == None:
            logging.error("Reading from stdin")
            args.file = sys.stdin

        if args.regname:
            return [self.load_one_registry(args)]
        else:
            return self.load_registry_from_file(args.file)

    def load_registry_from_file(self, path):
        """Load and return a registry list from the given path."""
        registries = XITTestRegistry.fromXML(path)
        if len(registries) == 0:
            logging.error("Failed to parse input file.")
            sys.exit(1)

        return registries

    def write_to_registry(self, f, msg):
        print >> f, msg

    def open_new_registry(self, args):
        if args.file == None or args.file == sys.stdin:
            return sys.stdout
        else:
            return os.tmpfile()

    def sync_registry(self, args, outfile):
        if outfile == sys.stdout:
            return

        outfile.seek(0)
        f = open(args.file, "w")
        shutil.copyfileobj(outfile, f)

    def registry_from_string(self, args, s):
        r = self.open_new_registry(args)
        self.write_to_registry(r, s)
        self.sync_registry(args, r)


    def run(self, cmdline_args = sys.argv[1:]):
        if not sys.stdout.isatty():
            termcolors.disable()

        parser = self.parse_cmdline()
        args = parser.parse_args(cmdline_args)

        args.func(args)
