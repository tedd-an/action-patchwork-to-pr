#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
import argparse
import shutil
import subprocess
import requests
import time
import re
import logging
from git import Repo

logger = None

PR_TITLE_PREFIX='PW_S_ID'

def requests_url(url):
    """ Helper function to requests GET with URL """
    resp = requests.get(url)
    if resp.status_code != 200:
        raise requests.HTTPError("GET {}".format(resp.status_code))

    return resp

def check_call(cmd, env=None, cwd=None, shell=False):
    """ Run command with arguments.  Wait for command to complete.

    Args:
        cmd (str): command to run
        env (obj:`dict`): environment variables for the new process
        cwd (str): sets current directory before execution
        shell (bool): if true, the command will be executed through the shell

    Returns:
        ret: Zero for success, otherwise raise CalledProcessError

    """
    logging.info("cmd: %s" % cmd)
    if env:
        logging.debug("env: %s" % env)

    if shell:
        cmd = subprocess.list2cmdline(cmd)

    return subprocess.check_call(cmd, env=env, cwd=cwd, shell=shell, stderr=subprocess.STDOUT)

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
        return proc.returncode
    elif stderr:
        return 1
    else:
        return 0

def apply_patches(repo_dir, patches):
    for patch in patches:
        ret = git("am", patch, cwd=repo_dir) != 0
        if ret != 0:
            logging.warning("Failed to apply patch. Abort am")
            git("am", "--abort", cwd=repo_dir)
            return ret

    return 0

def hub_create_pr(repo_dir, pr_msg, base_repo, base_branch, branch):
    dest = '{}:{}'.format(base_repo.split("/")[0], base_branch)
    #cmd = ['hub', 'pull-request', '--push', '--base', dest, '-F', pr_msg]
    cmd = ['hub', 'pull-request', '-b', dest, '-h', branch, '-F', pr_msg]
    return check_call(cmd, cwd=repo_dir)

def github_get_pr_list(base_repo):
    """
    Returns the list of opened pull request from the repo.
    The list contains only the pull request pr_id, html_url, and title.
    """

    url='https://api.github.com/repos/{}/pulls'.format(base_repo)
    logging.debug("URL: %s" % url)

    pr_list = []

    while True:
        resp = requests_url(url)
        pr_list = pr_list + resp.json()

        # Read next page
        if "next" not in resp.links:
            logging.debug("Read all pull requests: Total = %d" % len(pr_list))
            break

        logging.debug("Read next list")
        url = resp.links["next"]["url"]

    return pr_list

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

def search_series_in_pr_list(pr_list, series_id):
    """
    Return True if search series_id exists in pr_list.title.
    PR shoud have PW_S_ID:series_id in title.
    """
    prefix = '{}:{}'.format(PR_TITLE_PREFIX, series_id)
    for pr in pr_list:
        if re.search(prefix, pr["title"], re.IGNORECASE):
            return True
    return False

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

def create_pr_with_series(series_path, base_repo, base_branch):
    """ Create pull request with the patches in the series """

    logging.debug("ENV: HUB_PROTOCOL: %s" % os.environ["HUB_PROTOCOL"])
    logging.debug("ENV: GITHUB_USER: %s" % os.environ["GITHUB_USER"])
    logging.debug("ENV: GITHUB_TOKEN: %s" % os.environ["GITHUB_TOKEN"])

    series_path_list = get_dir_list(series_path)

    src_dir = os.path.abspath(os.path.curdir)
    logging.debug("Current Src Dir: %s" % src_dir)

    # Get current pr from the target repo
    pr_list = github_get_pr_list(base_repo)

    # Check out the base branch
    git("checkout", base_branch, cwd=src_dir)

    for series_path in series_path_list:
        logging.info("\n>> Series Path: %s" % series_path)

        # Read series json file
        json_file = os.path.join(series_path, "series.json")
        if not os.path.exists(json_file):
            logging.error("cannot find series detail: %s" % json_file)
            continue

        # Load series detail from series.json file
        with open(json_file, 'r') as jf:
            series = json.load(jf)
        logging.info("Series id: %d" % series["id"])

        branch = str(series["id"])

        # Check PR list if it already exist
        if search_series_in_pr_list(pr_list, series["id"]):
            logging.info("PR already exist. Skip creating PR")
            continue

        # Get list of patches from the pathces directory in series directory
        patch_path_list = get_dir_list(os.path.join(series_path, "patches"))
        if len(patch_path_list) == 0:
            logging.error("no patch file found from %s" % series_path)
            continue

        # create branch with series name
        git("checkout", "-b", branch, cwd=src_dir)

        # Apply patches
        if apply_patches(src_dir, patch_path_list) != 0:
            logging.error("failed to apply patch.")
            git("checkout", base_branch, cwd=src_dir)
            # TODO: send email to the submitter and reqeust to send after rebase
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

        # use hub to create pr
        try:
            hub_create_pr(src_dir, pr_msg, base_repo, base_branch, branch)
        except subprocess.CalledProcessError as e:
            logging.error("failed to create pr error=%d" % e.returncode)
            git("push", "origin", "--delete", branch, cwd=src_dir)

        # Check out to the target_branch
        git("checkout", base_branch, cwd=src_dir)

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

def main():
    args = parse_args()

    init_logging()

    create_pr_with_series(args.series_path,
                          args.base_repo,
                          args.base_branch)

if __name__ == "__main__":
    main()
