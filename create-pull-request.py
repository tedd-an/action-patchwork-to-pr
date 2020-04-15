#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
import argparse
import subprocess
import time
import re
import logging
import smtplib
import email.utils
from github import Github
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = None

github_repo = None

PR_TITLE_PREFIX='PW_SID'

AM_FAIL_MSG = '''
This is automated email and please do not reply to this email!!

Dear submitter,

Thanks for submitting the patches.
However, we encountered an issue while applying your patches to
the tip of the current master.

Base:
https://git.kernel.org/pub/scm/bluetooth/bluez.git/commit/?id={}

Patch Info:
URL: {}
Title: {}

Error message:
{}


---
Regards,
Linux Bluetooth Test Bot
'''

def git(*args, cwd=None):
    """ Run git command and return the return code. """

    cmd = ['git']
    cmd.extend(args)
    cmd_str = "{}".format(" ".join(str(w) for w in cmd))
    logging.info("GIT Command: '%s'" % cmd_str)

    try:
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                cwd=cwd)
    except OSError as e:
        print("ERROR: failed to run git cmd: '%s': %s" % (cmd_str, e))
        return -1

    stdout, stderr = proc.communicate()
    stdout = stdout.decode("utf-8")
    stderr = stderr.decode("utf-8")
    logging.debug(">> stdout")
    logging.debug("{}".format(stdout))
    logging.debug(">> stderr")
    logging.debug("{}".format(stderr))

    # Return error
    if proc.returncode:
        return (proc.returncode, stdout, stderr)
    elif stderr:
        return (1, stdout, stderr)
    else:
        return (0, stdout, stderr)

def send_email(sender, receiver, msg):
    """ Send email """
    if 'EMAIL_TOKEN' not in os.environ:
        logging.warning("missing EMAIL_TOKEN. Skip sending email")
        return
    try:
        session = smtplib.SMTP('smtp.gmail.com', 587)
        session.ehlo()
        session.starttls()
        session.ehlo()
        session.login(sender, os.environ['EMAIL_TOKEN'])
        session.sendmail(sender, receiver, msg.as_string())
    except Exception as e:
        logging.error("Exception: {}".format(e))
    finally:
        session.quit()

def find_patch_details(patch_file, series):
    """ Find patch details from patch_file using name """

    subject = None

    # Find line with 'Subject: '
    with open(patch_file, 'r') as pf:
        for line in pf:
            line = line.strip(" \t")
            if re.search("Subject: ", line):
                logging.debug("Found Subject line: {}".format(line))
                # If there is no [ in the subject, just take it as is
                if line.find("[") == -1:
                    substr = re.search(r'^Subject: (.+)', line)
                else:
                    # Remove "Subject: [*] "
                    substr = re.search(r'^Subject: \[.+\] (.+)', line)
                if substr:
                    subject = substr.group(1)
                    break

    if subject == None:
        logging.warning("Cannot find the Subject line from the patch. Abort")
        return None

    # Search subject from the patch list in the series
    patches = series['patches']
    for patch in patches:
        if re.search(subject, patch['name']):
            logging.debug("Found matching patch detail")
            return patch

    return None

def notify_am_fail(repo_dir, patch_file, series, stdout, stderr):
    """ Send git-am failure email """
    sender = 'bluez.test.bot@gmail.com'

    # TODO: For testing purpose, use temp email
    receivers = 'tedd.an@intel.com'

    patch = find_patch_details(patch_file, series)
    if patch == None:
        logging.error("Failed to get patch detail. skip sending email")

    (ret, master_id, err) = git("rev-parse", "origin/master", cwd=repo_dir)
    if ret !=0 :
        logging.error("Failed to get the commit-id of master: {}".format(err))
        return

    # Generate message
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = receivers
    msg['Subject'] = "RE: {}".format(patch['name'])
    msg.add_header('In-Reply-To', patch['msgid'])
    msg.add_header('References', patch['msgid'])

    content = AM_FAIL_MSG.format(master_id,
                                 patch['web_url'],
                                 patch['name'],
                                 stderr)
    msg.attach(MIMEText(content, 'plain'))
    logging.debug("Mail Message: {}".format(msg))

    logging.info("Sending am fail email: msg: {}".format(patch['name']))
    send_email(sender, receivers, msg)

def apply_patches(repo_dir, series, patches):
    for patch in patches:
        (ret, stdout, stderr) = git("am", patch, cwd=repo_dir)
        if ret != 0:
            git("am", "--abort", cwd=repo_dir)
            logging.warning("Failed to apply patch. Notify and abort")
            notify_am_fail(repo_dir, patch, series, stdout, stderr)
            output = "stdout:\n{}\nstderr:\n{}".format(stdout, stderr)
            github_create_issue(series, output)
            return ret

    return 0

def github_create_pr(pr_msg, base, head):

    body = ""
    with open(pr_msg, "r") as f:
        title = f.readline()
        for line in f:
            body += line

    logging.debug("Creating PR: {} <-- {}".format(base, head))
    pr = github_repo.create_pull(title=title, body=body, base=base, head=head,
                                 maintainer_can_modify=True)
    logging.info("PR created: PR:{} URL:{}".format(pr.number, pr.url))

def github_close_pr(pr_num):
    """
    Delete PR and delete associated branch
    """
    pr = github_repo.get_pull(pr_num)
    pr_head_ref = pr.head.ref
    logging.debug("Closing PR({})".format(pr_num))

    pr.edit(state="closed")
    logging.debug("PR({}) is closed".format(pr_num))

    git_ref = github_repo.get_git_ref("heads/{}".format(pr_head_ref))
    git_ref.delete()
    logging.debug("Branch({}) is removed".format(pr_head_ref))

def github_create_issue(series, output):
    """
    Create github issue for the series that cannot create the pr such as
    git am fail.
    The header is same as PR
    """
    title = '[{}:{}] {}'.format(PR_TITLE_PREFIX, series["id"], series["name"])
    logging.debug("Creating ISSUE: title={}".format(title))
    issue = github_repo.create_issue(title=title, body=output)
    logging.info("Issue created: Num:{} URL:{}".format(issue.number, issue.url))

def github_close_issue(issue_num):
    """
    Delete Issue from the github
    """
    issue = github_repo.get_issue(issue_num)
    logging.debug("Closing Issue({})".format(issue_num))

    issue.edit(state="closed")
    logging.debug("Issue({}) is closed".format(issue_num))

def get_dir_list(base_dir):
    """ Get the list of absolute path of directory """
    dir_list = []

    base_abs_dir = os.path.join(os.path.curdir, base_dir)

    logging.debug("Dir List: %s" % base_abs_dir)
    for item in sorted(os.listdir(base_abs_dir)):
        item_path = os.path.join(base_abs_dir, item)
        dir_list.append(os.path.abspath(item_path))
        logging.debug("   %s" % os.path.abspath(item_path))

    return dir_list

def find_sid_in_prs(pr_list, sid):
    """
    Return True if sid exists in title of PR in the format of [PW_S_ID:sid].
    """
    prefix = '{}:{}'.format(PR_TITLE_PREFIX, sid)
    for pr in pr_list:
        if re.search(prefix, pr.title, re.IGNORECASE):
            return True
    return False

def find_sid_in_issues(issue_list, sid):
    """
    Return True if sid exists in title of Issue in the format of [PW_S_ID:sid].
    """
    prefix = '{}:{}'.format(PR_TITLE_PREFIX, sid)
    for issue in issue_list:
        if re.search(prefix, issue.title, re.IGNORECASE):
            return True
    return False

def find_sid_in_series(sid, series_dir):
    """
    Search @sid from @series_dir and return True if the folder exist,
    otherwise return False
    """
    for series in series_dir:
        if re.search(sid, series):
            logging.debug("Found s_id({}) in series dir".format(sid))
            return True
    logging.debug("Cannot find s_id({}) in series dir".format(sid))
    return False

def get_pw_sid(pr_title):
    """
    Parse PR title prefix and get PatchWork Series ID
    PR Title Prefix = "[PW_S_ID:<series_id>] XXXXX"
    """
    try:
        sid = re.search(r'^\[PW_SID:([0-9]+)\]', pr_title).group(1)
    except AttributeError:
        logging.error("Unable to find the series_id from title %s" % pr_title)
        sid = None
    return sid

def generate_pr_msg(series, series_path, patch_path_list):
    """
    Generatre PR message file and return the path.
    The first line is the message title includes PR_TITLE_PREFIX.
    The message is extracted from the cover_letter. If the cover_letter
    doesn't exist, use the first patch commit message.
    """
    pr_title = '[{}:{}] {}'.format(PR_TITLE_PREFIX,
                                   series["id"],
                                   series["name"])

    patch_file = patch_path_list[0]
    if os.path.exists(os.path.join(series_path, "cover_letter")):
        patch_file = os.path.join(series_path, "cover_letter")
    logging.info("Patch File: %s" % patch_file)

    commit_msg = ""
    with open(patch_file, 'r') as pf:
        save_msg = False
        for line in pf:
            line = line.strip(" \t")
            if line == os.linesep and save_msg == False:
                logging.debug("Commit message start - first space")
                save_msg = True
                continue
            if re.search("---", line):
                logging.debug("Commit message end - \'---\'")
                break

            if save_msg == True:
                logging.debug("   commit msg: %s" % line)
                commit_msg += line

    # Create pr_msg file
    pr_msg = os.path.abspath(os.path.join(series_path, "pr_msg"))
    f = open(pr_msg, "w")
    f.write(pr_title + os.linesep)
    f.write(os.linesep)
    f.write(commit_msg)
    f.close()
    return pr_msg

def read_series_json(series_path):
    """ Read series' json file and return the json object """

    json_file = os.path.join(series_path, "series.json")
    if not os.path.exists(json_file):
        logging.error("cannot find series detail: %s" % json_file)
        return None

    series = None

    # Load series detail from series.json file
    with open(json_file, 'r') as jf:
        series = json.load(jf)
    return series

def clean_up_pr(series_path_list):
    """ Clean up PR if it doesn't exist in series """
    # Get PR list
    pr_list = github_repo.get_pulls()

    for pr in pr_list:
        pw_sid = get_pw_sid(pr.title)
        logging.debug("Checking PR({}): Series({})".format(pr.number, pw_sid))
        # search pw_sid from the series path
        if not find_sid_in_series(pw_sid, series_path_list):
            # PR is old and need to remove
            logging.debug("No serires found. PR needs to be closed")
            github_close_pr(pr.number)
            continue

def clean_up_issues(series_path_list):
    """ Clena up Issues if it doens't exist in series """
    # Get Issue list
    issue_list = github_repo.get_issues()

    for issue in issue_list:
        pw_sid = get_pw_sid(issue.title)
        logging.debug("Checking Issue({}): Series({})".format(issue.number,
                                                              pw_sid))
        # search pw_sid from the series path
        if not find_sid_in_series(pw_sid, series_path_list):
            # Issue is old and need to remove
            logging.debug("No serires found. Issue can to be closed")
            github_close_issue(issue.number)
            continue

def manage_pull_request(series_path, base_repo, base_branch):
    """ Create pull request with the patches in the series """

    logging.debug("ENV: HUB_PROTOCOL: %s" % os.environ["HUB_PROTOCOL"])
    logging.debug("ENV: GITHUB_USER: %s" % os.environ["GITHUB_USER"])

    series_path_list = get_dir_list(series_path)

    src_dir = os.path.abspath(os.path.curdir)
    logging.debug("Current Src Dir: %s" % src_dir)

    # Get current pr from the target repo
    pr_list = github_repo.get_pulls()

    # Get current issue from the target repo
    issue_list = github_repo.get_issues()

    # Check out the base branch
    git("checkout", base_branch, cwd=src_dir)

    for series_path in series_path_list:
        logging.info("\n>> Series Path: %s" % series_path)

        # Read series json file
        series = read_series_json(series_path)
        if series == None:
            logging.warning("Failed to read series json file")
            continue

        logging.info("Series id: %d" % series["id"])
        branch = str(series["id"])

        # Check if PR is already created
        if find_sid_in_prs(pr_list, series["id"]):
            logging.info("PR already exist. Skip creating PR")
            continue

        # Check if Issue is already created
        if find_sid_in_issues(issue_list, series["id"]):
            logging.info("Issue already exist. Skip further checking")
            continue

        # Get list of patches from the pathces directory in series directory
        patch_path_list = get_dir_list(os.path.join(series_path, "patches"))
        if len(patch_path_list) == 0:
            logging.error("no patch file found from %s" % series_path)
            continue

        # create branch with series name
        git("checkout", "-b", branch, cwd=src_dir)

        # Apply patches
        if apply_patches(src_dir, series, patch_path_list) != 0:
            logging.error("failed to apply patch.")
            git("checkout", base_branch, cwd=src_dir)
            continue

        try:
            git("push", "origin", branch, cwd=src_dir)
        except subprocess.CalledProcessError as e:
            logging.error("failed to push %s error=%d " % (branch, e.returncode))
            git("checkout", base_branch, cwd=src_dir)
            continue

        # Prepare PR message if cover_letter exist
        pr_msg = generate_pr_msg(series, series_path, patch_path_list)

        time.sleep(1)

        github_create_pr(pr_msg, base_branch, branch)

        # Check out to the target_branch
        git("checkout", base_branch, cwd=src_dir)

    clean_up_pr(series_path_list)

def parse_args():
    """ Parse input argument """
    ap = argparse.ArgumentParser(
        description="Create the pull-request to the github repository"
    )

    ap.add_argument("-s", "--series-path", default="./series",
                    help="Folder contains the patch series.")

    ap.add_argument("-r", "--base-repo", required=True,
                    help="Name of base repo where the PR is pushed. "
                         "Use <OWNER>/<REPO> format. i.e. bluez/bluez")

    ap.add_argument("-b", "--base-branch", default="master",
                    help="Name of branch in base_repo where the PR is pushed. "
                         "Use <BRANCH> format. i.e. master")

    args = ap.parse_args()

    return args

def init_logging():
    """ Initialize logger """

    global logger

    logger = logging.getLogger('')
    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(levelname)-8s:%(funcName)s(%(lineno)d): %(message)s')
    ch.setFormatter(formatter)

    logger.addHandler(ch)
    logger.setLevel(logging.DEBUG)
    logging.info("Initialized the logger")

def init_github(args):

    global github_repo
    github_repo = Github(os.environ['GITHUB_TOKEN']).get_repo(args.base_repo)

def main():
    args = parse_args()

    init_logging()

    init_github(args)

    manage_pull_request(args.series_path, args.base_repo, args.base_branch)

if __name__ == "__main__":
    main()
