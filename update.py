#!/usr/bin/python3

import cgi
import cgitb
import dnf
import rpm
import koji
import re
import datetime
import concurrent.futures
import configparser
import urllib.request
import io
from dnf.subject import Subject
import hawkey
from copr.v3 import Client
import threading
import time
import os
import sys
import subprocess

class CoprResults:
    def __init__(self, url, owner, project):
        self.url = url
        config = {'copr_url': url  }
        self.client = Client(config)
        self.owner = owner
        self.project = project
        try:
            self.client.base_proxy.home()
            self.packages = executor.submit(self.get_packages, clang_gcc_br_pkgs_fedora)
        except Exception as e:
            print(project, str(e), file = sys.stderr)
            self.packages = executor.submit(lambda : {})

    def get_build_link(self, pkg_id):
        return '{}/coprs/{}/{}/build/{}/'.format(self.url, self.owner.replace('@','g/'), self.project, pkg_id)

    def get_package_base_link(self):
        return '{}/coprs/{}/{}/package/'.format(self.url, self.owner.replace('@','g/'), self.project)

    def get_package_link(self, pkg):
        return '{}{}'.format(self.get_package_base_link(), pkg.name)

    def get_packages(self, clang_gcc_br_pkgs_fedoran):
        pkgs = {}
        for p in self.client.package_proxy.get_list(self.owner, self.project, with_latest_succeeded_build=True, with_latest_build=True):
            build_passes = True
            pkg =  p['builds']['latest_succeeded']
            if not pkg:
                pkg =  p['builds']['latest']
                if not pkg:
                    continue
                build_passes = False
            pkg['copr'] = self
            src_version = pkg['source_package']['version']
            pkg['nvr'] = "{}-{}".format(p['name'], src_version)
            pkg['name'] = p['name']
            pkgs[p['name']] = CoprPkg(pkg, self, build_passes)
        return pkgs

    def get_file_prefix(self, is_baseline):
        if is_baseline:
            return None
        return self.project

class KojiResults:
    def __init__(self, tag, koji_url = 'https://koji.fedoraproject.org/kojihub'):
        self.tag = tag
        self.session = koji.ClientSession(koji_url)
        try:
            self.session.hello()
            self.packages = executor.submit(self.get_packages, clang_gcc_br_pkgs_fedora)
        except Exception as e:
            print(tag, str(e), file = sys.stderr)
            self.packages = executor.submit(lambda : {})


    def get_package_base_link(self):
        return "'https://koji.fedoraproject.org/koji/search?type=package&match=glob&terms="

    def get_packages(self, clang_gcc_br_pkgs_fedora):
        clang_gcc_br_pkgs = clang_gcc_br_pkgs_fedora
        tag = "{}-updates".format(self.tag)
        pkgs = {}
        for p in self.session.listTagged(tag = tag, inherit = True, latest = True):
            if not p['tag_name'].startswith(self.tag):
                continue
            if p['name'] not in clang_gcc_br_pkgs.result():
                continue

            pkgs[p['name']] = KojiPkg(p, 'https://koji.fedoraproject.org/koji/')
        return pkgs

    def get_file_prefix(self, is_baseline):
        if not is_baseline:
            return None
        return self.tag

def remove_dist_tag(pkg):
    return pkg.get_nvr_without_dist()

def remove_epoch(pkg):
    subject = Subject(pkg)
    try:
        nevr = subject.get_nevra_possibilities(forms=hawkey.FORM_NEVR)[0]
        return "{}-{}-{}".format(nevr.name, nevr.version, nevr.release)
    except:
        print("Cannot remove epoch for ", pkg, sys.stderr)
        return pkg

def get_package_link(koji_url, pkg):
    return "{}/search?type=package&match=glob&terms={}".format(
            koji_url, pkg)

def get_build_link(koji_url, pkg, search_str = None):
    return pkg.get_build_link(search_str)

def get_base_tag(tag):
    return tag.split('-')[0]

def tag_to_dist_prefix(tag):
    basetag = get_base_tag(tag)
    if basetag.startswith('f'):
        return "fc{}".format(basetag[1:])

    # eln
    return basetag

def get_build_link_with_different_dist(koji_url, pkg):
    if 'copr' in pkg:
        return pkg['copr'].get_build_link(pkg)
    tag = pkg['tag_name']
    nvr = pkg['nvr']
    #Remove everything after the dist-tag
    dist_prefix = tag_to_dist_prefix(tag)
    dist_start = nvr.find(dist_prefix)
    dist_prefix_end = dist_start + len(dist_prefix)
    search_str = nvr[0:dist_prefix_end]
    return get_build_link(koji_url, pkg, search_str)

class Pkg:
    def __init__(self, name, nvr, build_passes = True):
        self.name = name
        self.nvr = nvr
        self.build_passes = build_passes


class KojiPkg(Pkg):
    def __init__(self, pkg, koji_weburl):
        super(KojiPkg, self).__init__(pkg['name'], pkg['nvr'])
        self.pkg = pkg
        self.koji_weburl = koji_weburl

    def get_nvr_without_dist(self):
        return re.sub('\.[^.]+$','', self.nvr)

    def get_build_link(self, search_str = None):
        if not search_str:
            build = self.pkg['build_id']
            return "{}/buildinfo?buildID={}".format(self.koji_weburl, build)
        return "{}/search?type=build&match=regexp&terms={}".format(
                self.koji_weburl, search_str)
    
    def get_package_base_link(self):
        return "{}/search?type=package&match=glob&terms=".format(
                self.koji_weburl)

    def get_package_link(self):
        return "{}{}".format(self.get_package_base_link(), self.name)


class CoprPkg(Pkg):
    def __init__(self, pkg, copr_results, build_passes):
        super(CoprPkg, self).__init__(pkg['name'], pkg['nvr'], build_passes)
        self.pkg = pkg
        self. copr_results = copr_results
    
    def get_nvr_without_dist(self):
        return self.nvr

    def get_package_base_link(self):
        return self.copr_results.get_package_base_link()
    
    def get_build_link(self, koji_url, search_str = None):
        return self.copr_results.get_build_link(self.pkg['id'])

    def get_package_link(self):
        self.copr_results.get_package_link(self)


# https://koji.fedoraproject.org/koji/
class PkgCompare:

    STATUS_REGRESSION = 0
    STATUS_MISSING = 1
    STATUS_OLD = 2
    STATUS_FIXED = 3
    STATUS_FAILED = 4
    STATUS_PASS = 5

    def __init__(self, pkg):
        self.pkg = pkg
        self.other_pkg = None
        self.note = None

    def add_other_pkg(self, pkg):
        self.other_pkg = pkg

    def add_note(self, note):
        self.note = note

    def compare_nvr(self, a_nvr, b_nvr):
        subject_a = Subject(a_nvr)
        subject_b = Subject(b_nvr)
        nevra_a = subject_a.get_nevra_possibilities(forms=hawkey.FORM_NEVR)[0]
        nevra_b = subject_b.get_nevra_possibilities(forms=hawkey.FORM_NEVR)[0]
        return rpm.labelCompare(("", nevra_a.version, nevra_a.release), ("", nevra_b.version, nevra_b.release))

    def is_up_to_date(self):
        if not self.other_pkg:
            return False
        if not self.other_pkg.build_passes:
            return False
        pkg_nvr = remove_epoch(remove_dist_tag(self.pkg))
        other_nvr = remove_epoch(remove_dist_tag(self.other_pkg))

        return self.compare_nvr(pkg_nvr, other_nvr) <= 0

    def get_pkg_status(self):
        if not self.pkg.build_passes:
            return self.STATUS_FAILED
        else:
            return self.STATUS_PASS

    def get_other_pkg_status(self):
        if not self.other_pkg:
            return self.STATUS_MISSING

        if not self.other_pkg.build_passes:
            if self.get_pkg_status() == self.STATUS_PASS:
                return self.STATUS_REGRESSION
            return self.STATUS_FAILED

        # other_pkg build passes
        if not self.is_up_to_date():
            return self.STATUS_OLD

        # other pkg is up-to-date.
        if self.get_pkg_status() == self.STATUS_FAILED:
            return self.STATUS_FIXED

        return self.STATUS_PASS


    def html_row(self, index, pkg_notes = None):
        row_style=''
        clang_nvr=''
        build_success = False
        has_note = False

        if index % 2 == 0:
            row_style=" class='even_row'"
        if self.other_pkg:
            clang_nvr = self.other_pkg.nvr

        column2 = ''
        if not self.is_up_to_date():
            column2 = """<form target='_blank' class="form_cell" method='post' action='https://jenkins-llvm-upstream-ci.apps.ocp4.prod.psi.redhat.com/job/Koji%20Shadow%20Clang/build'>
                            <input name='json' type='hidden' value="{{'parameter': [{{'name' : 'KOJI_NVR', 'value' : '{}' }}, {{'name' : 'CI_MESSAGE', 'value' : ''}}], 'statusCode': '303', 'redirectTo': '/job/Koji%20Shadow%20Clang/'}}" />
                           <input name='Rebuild' type='submit' value='Rebuild' />
                         </form>""".format(self.pkg.nvr)
            if use_copr:
                column2 = "<a target='_blank' href='{}/rebuild'>Rebuild</a>".format(self.package_base_link + self.pkg.name)

        clang_latest_build_link = ''
        clang_latest_build_text = ''
        column3 = ''
        column4 = ''
        status = self.get_other_pkg_status()
        if status == self.STATUS_FIXED or status == self.STATUS_PASS:
            column3 = "<a href='{link}'><span class='tooltip'>{clang_nvr}</span>{clang_nvr}</a>".format(
                    link = get_build_link('http://clang-koji-web.usersys.redhat.com/koji/', self.other_pkg),
                    clang_nvr = clang_nvr)
            column4 = "SAME"
            build_success = True
        else:
            if use_copr:
                if status == self.STATUS_MISSING or status == self.STATUS_OLD:
                    url = self.package_base_link + self.pkg.name
                    text = 'MISSING'
                else:
                    url = get_build_link('', self.other_pkg)
                    text = 'FAILED'

            else:
                url = get_build_link_with_different_dist('http://clang-koji-web.usersys.redhat.com/koji/', self.pkg)
                text = 'MISSING OR FAILED'
            column3 = "<a href='{link}'>{text}</a>".format(link=url, text=text)

            if status == self.STATUS_OLD:
                column4 = "<a href='{link}'><span class='tooltip'>{clang_nvr}</span>{clang_nvr}</a>".format(
                        link = get_build_link('http://clang-koji-web.usersys.redhat.com/koji/', self.other_pkg),
                        clang_nvr = clang_nvr)
                build_success = True
            else:
                column4 = "NONE"

        note = ""
        short_note = ""
        if self.note:
            has_note = True
            note = self.note
            short_note = self.note

        history_url='http://clang-koji-web.usersys.redhat.com/koji/search?type=package&match=glob&terms={}'.format(self.pkg.name)
        if use_copr:
            history_url = self.package_base_link + self.pkg.name
        history="<a href='{history_url}'>[Build History]</a></td>".format(history_url=history_url)
        if not use_copr and not self.other_pkg:
           history=""

        if not has_note and not build_success:
            row_style=" class='todo_row'"
        else:
            stats.num_pass_or_note += 1

        return """
            <tr{row_style}>
              <td class='pkg_cell'><a href='{fedora_build_url}'><span class='tooltip'>{nvr}</span>{nvr}</a></td>
              <td>{column2}</td>
              <td class='pkg_cell'>{column3}</td>
              <td class='pkg_cell' style='max-width: 20ch;'>{column4}</td>
              <td>{history}</td>
              <td class='pkg_cell'><span class='tooltip'>{note}</span>{short_note}</td>
            </tr>""".format(row_style =row_style,
                            fedora_build_url = get_build_link('https://koji.fedoraproject.org/koji/', self.pkg),
                            nvr = self.pkg.nvr + (' (FAILED)' if use_copr and not self.pkg.build_passes else ''),
                            column2 = column2,
                            column3 = column3,
                            column4 = column4,
                            pkg_name = self.pkg.name,
                            history = history,
                            note = note, short_note = short_note)

class Stats:
    def __init__(self):
        self.num_fedora_pkgs = 0
        self.num_clang_pkgs = 0
        self.num_up_to_date_pkgs = 0
        self.num_pass_or_note = 0

        self.num_regressions = 0
        self.num_fixed = 0
        self.num_missing = 0

    def html_color_for_percent(percent):
        return 'black'
        if percent < 33.3:
            return 'red'
        if percent < 66.7:
            return '#CC9900'
        return 'green'


    def html_table(self):
        clang_percent = 100 * (self.num_clang_pkgs / self.num_fedora_pkgs)
        clang_percent_color = Stats.html_color_for_percent(clang_percent)
        up_to_date_percent = 100 * (self.num_up_to_date_pkgs / self.num_fedora_pkgs)
        up_to_date_percent_color = Stats.html_color_for_percent(clang_percent)
        pass_or_note_percent = 100 * (self.num_pass_or_note / self.num_fedora_pkgs)
        regression_percent = 100 * (self.num_regressions / self.num_fedora_pkgs)
        fixed_percent = 100 * (self.num_fixed / self.num_fedora_pkgs)
        missing_percent = 100 * (self.num_missing / self.num_fedora_pkgs)
        num_chars = len(str(self.num_fedora_pkgs))


        return """
            <table class='stats_table even_row'>
              <tr><th colspan='3'>Summary</th></tr>
              <tr><td>Fedora Packages:</td><td style='text-align: right;'>{}</td><td></td></tr>
              <tr><td>Clang Builds:</td><td style='text-align: right; width:{num_chars}ch'>{}</td><td class='stats_per_col' style='color:{}'>{:.1f}%</td></tr>
              <tr><td>Clang Builds Latest:</td><td style='text-align: right; width:{num_chars}ch'>{}</td><td class='stats_per_col' style='color:{}'>{:.1f}%</tr>
              <tr><td>Clang Builds Or Has Note:</td><td style='text-align: right; width:{num_chars}ch'>{}</td><td class='stats_per_col'>{:.1f}%</tr>
              <tr><td>Regressions: :</td><td style='text-align: right; width:{num_chars}ch'>{}</td><td class='stats_per_col'>{:.1f}%</tr>
              <tr><td>Fixed:</td><td style='text-align: right; width:{num_chars}ch'>{}</td><td class='stats_per_col'>{:.1f}%</tr>
              <tr><td>Missing:</td><td style='text-align: right; width:{num_chars}ch'>{}</td><td class='stats_per_col'>{:.1f}%</tr>
            </table>""".format(self.num_fedora_pkgs,
                    self.num_clang_pkgs, clang_percent_color, clang_percent,
                    self.num_up_to_date_pkgs,up_to_date_percent_color, up_to_date_percent,
                    self.num_pass_or_note, pass_or_note_percent,
                    self.num_regressions, regression_percent,
                    self.num_fixed, fixed_percent,
                    self.num_missing, missing_percent,
                    num_chars = num_chars)


def get_html_header():
    return """
<html>
  <head>
    <link rel="preload" href="https://static.redhat.com/libs/redhat/redhat-font/2/webfonts/RedHatText/RedHatText-Regular.woff" as="font" type="font/woff" crossorigin>
    <link type="text/css" rel="stylesheet" href="https://static.redhat.com/libs/redhat/redhat-theme/5/advanced-theme.css" media="all" />
    <link type="text/css" rel="stylesheet" href="https://static.redhat.com/libs/redhat/redhat-font/2/webfonts/red-hat-font.css" media="all" />
    <style>
      .redhat_font {
        font-family: "RedHatText", "Overpass", Overpass, Helvetica, Arial, sans-serif;
      }
      .stats_table {
        font-family: "RedHatDisplay", "Overpass", Overpass, Helvetica, Arial, sans-serif;
      }
      .stats_table th {
        background-color: #252525;
        border: 0px;
      }
      .stats_table td {
        border: 0px;
      }
      .stats_per_col {
        width: 4ch;
        text-align: right;
      }
      .even_row {
        background-color: #DCDCDC;
      }
      .todo_row {
        background-color: #f9ebea;
      }
      th {
        background-color: #0066cc;
        color: #ffffff;
      }
      th, td {
        border-right: 4px solid white;
        max-width: 30ch;
        overflow: hidden;
        white-space: nowrap;
      }
      .pkg_cell .tooltip {
        visibility: hidden;
        position: absolute;
        z-index: 1;
      }
      .pkg_cell:hover .tooltip {
        visibility: visible;
      }
      .last_updated {
        font-size: 0.8em;
        margin-top: 20px;
        margin-bottom: 20px;
        display: inline-block;
      }
      .form_cell {
        margin: 0;
        padding: 0;
      }
    </style>
  </head>
  <body class='redhat_font'>"""


def get_gcc_clang_users_fedora():
    # Repo setup
    base = dnf.Base()
    conf = base.conf
    for compose in ['BaseOS', 'AppStream', 'CRB', 'Extras']:
        base.repos.add_new_repo(f'eln-{compose}-source', conf, baseurl=[f'https://odcs.fedoraproject.org/composes/production/latest-Fedora-ELN/compose/{compose}/source/tree/'])
    repos = base.repos.get_matching('*')
    repos.disable()
    repos = base.repos.get_matching('eln-*-source')
    repos.enable()

    # Find all the relevant packages
    base.fill_sack()
    q = base.sack.query()
    q = q.available()
    q = q.filter(requires=['gcc', 'gcc-c++', 'clang'])
    return set([p.name for p in list(q)])

def update_status(mutex):
    while not mutex.locked():
        time.sleep(10)
        print("Processing...")
        sys.stdout.flush()

cgitb.enable()

# Return something right away so the server doesn't timeout.
print("Content-Type: text/html")
print("")
print("<!DOCTYPE HTML><html><head>")
sys.stdout.flush()

executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

# Run a thread to periodically write to stdout to avoid the web server
# timing out.  There's probably a way to increase the web server timeout
# but I could not figure it out.
status_mutex = threading.Lock()
executor.submit(update_status, status_mutex)

clang_gcc_br_pkgs_fedora = executor.submit(get_gcc_clang_users_fedora)

# Exclude clang and llvm packages.
package_exclude_list = [
    'clang',
    'compiler-rt',
    'libomp',
    'lld',
    'lldb',
    'llvm'
]

form = cgi.FieldStorage()
tags = ['f35', 'f36', 'clang-built-f36']
if "tag" in form:
    tag = form['tag'].value
    if tag in tags:
        tags = [tag]

use_copr = True

comparisons = [
(KojiResults('f35'), CoprResults(u'https://copr.fedorainfracloud.org', '@fedora-llvm-team', 'clang-built-f35')),
(KojiResults('f36'), CoprResults(u'https://copr.fedorainfracloud.org', '@fedora-llvm-team', 'clang-built-f36')),
(CoprResults(u'https://copr.fedorainfracloud.org', '@fedora-llvm-team', 'clang-built-f35'), CoprResults(u'https://copr.fedorainfracloud.org', '@fedora-llvm-team', 'clang-built-f36')),
]

# Assume copr-reporter is in the current directory

if len(tags) !=1 and os.path.isdir('./copr-reporter'):

    pages = ['f36']

    print("COPR REPORTER", pages)
    old_cwd = os.getcwd()
    os.chdir('./copr-reporter')

    for p in pages:
        print("COPR REPORTER", p)
        subprocess.call('python3 ./json_generator.py {}.ini'.format(p), shell = True)
        subprocess.call('python3 ./html_generator.py', shell = True)
        subprocess.call('cp report.html {}/copr-reporter-{}.html'.format(old_cwd, p), shell = True)

    os.chdir(old_cwd)

for results in comparisons:
    stats = Stats()

    file_prefix = results[0].get_file_prefix(True)
    if not file_prefix:
        file_prefix = results[1].get_file_prefix(False)

    if file_prefix not in tags:
        continue

    baseline_pkgs = results[0].packages
    test_pkgs = results[1].packages

    # Filter out packages form exclude list
    baseline_pkgs = baseline_pkgs.result()
    for p in package_exclude_list:
        if p in baseline_pkgs:
            del baseline_pkgs[p]

    pkg_compare_list = []
    for p in sorted(baseline_pkgs.keys()):
        pkg_compare_list.append(PkgCompare(baseline_pkgs[p]))

    stats.num_fedora_pkgs = len(pkg_compare_list)
    test_pkgs = test_pkgs.result()

    if len(baseline_pkgs) == 0 or len(test_pkgs) == 0:
        print('Failed:', file_prefix, file = sys.stderr)
        f = open('{}-status.html'.format(file_prefix), 'w')
        f.write(get_html_header())
        f.write('Failed to load package lists')
        f.write("""
          <form style="display: inline;" action="update.py">
            <input type="hidden" name="tag" value="{}" />
            <input type="submit" value="Update">
          </form>""")
        f.write("</body></html>")
        f.close()
        continue

    for c in pkg_compare_list:

        c.package_base_link = results[1].get_package_base_link()

        test_pkg = test_pkgs.get(c.pkg.name, None)
        if not test_pkg:
            stats.num_missing += 1
            continue

        if test_pkg.build_passes:
            stats.num_clang_pkgs += 1
            stats.num_pass_or_note += 1
        elif c.note:
            stats.num_pass_or_note +=1

        c.add_other_pkg(test_pkg)
        if c.is_up_to_date():
            stats.num_up_to_date_pkgs += 1

        status = c.get_other_pkg_status()
        if status == c.STATUS_REGRESSION:
            stats.num_regressions += 1
        elif status == c.STATUS_FIXED:
            stats.num_fixed += 1

    f = open('{}-status.html'.format(file_prefix), 'w')
    f.write(get_html_header())

    f.write("""
    <a href='f35-status.html'>Fedora 35</a>
    <a href='f36-status.html'>Fedora 36</a>
    <a href='clang-built-f36-status.html'>Clang f35 vs f36</a>(<a href='copr-reporter-f36.html'>Detailed</a>)
    """)

    f.write(stats.html_table())
    f.write("""
        <form style="display: inline;" action="update.py">
          <input type="hidden" name="tag" value="{}" />
          <input type="submit" value="Update">
        </form>
          <div class="last_updated">Last Updated: <div id='timestamp' style="display: inline-block;">{}</div></div>
            <script>
              var date = new Date(document.getElementById("timestamp").innerHTML);
              document.getElementById("timestamp").innerHTML = date.toString();
            </script>""".format(file_prefix, datetime.datetime.utcnow().strftime("%m/%d/%Y %H:%M:%S UTC")))
    f.write("""
        <table>
          <tr><th colspan='2'>Fedora</th><th colspan='4'>Fedora Clang</th></tr>
          <tr><th colspan='2'>Latest Build</th><th>Latest Build</th><th>Latest Success</th><th></th><th>Notes</th>""")
    for index, c in enumerate(pkg_compare_list):
        f.write(c.html_row(index))
    f.write("</table></body></html>")

    f.close()

status_mutex.acquire()
executor.shutdown(True)

page_redirect='index.html'
if len(tags) == 1:
    page_redirect="{}-status.html".format(tags[0])

print ("""
    <meta http-equiv="refresh" content="0; url={redirect}">
  </head>
  <body>
    <a href="{redirect}">View Updated Page</a>
  </body>
</html>""".format(redirect = page_redirect))
