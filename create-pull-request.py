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
from git import Repo

PR_TITLE_PREFIX='PW_S_ID'
WORKING_BRANCH = 'master'

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
    print("cmd: %s" % cmd)
    if env:
        print("env: %s" % env)

    if shell:
        cmd = subprocess.list2cmdline(cmd)

    return subprocess.check_call(cmd, env=env, cwd=cwd, shell=shell, stderr=subprocess.STDOUT)

def git_clone(repo, directory, branch=None):
    cmd = ['git', 'clone']
    if branch != None:
        cmd.append('-b')
        cmd.append(branch)
    cmd.append(repo)
    cmd.append(directory)
    return check_call(cmd)

def git_checkout(repo_dir, branch, create=False):
    cmd = ['git', 'checkout']
    if create:
        cmd.append('-b')
    cmd.append(branch)
    return  check_call(cmd, cwd=repo_dir)

def git_am(repo_dir, patches):
    cmd = ['git', 'am']
    for patch in patches:
        cmd.append(patch)

    try:
        ret = check_call(cmd, cwd=repo_dir)
    except subprocess.CalledProcessError as e:
        # roll back
        cmd = ['git', 'am', '--abort']
        check_call(cmd, cwd=repo_dir)
        return e.returncode
    return ret

def git_push(repo_dir, branch, delete=False):
    cmd = ['git', 'push', 'origin']
    if delete:
        cmd.append('--delete')
    cmd.append(branch)
    return check_call(cmd, cwd=repo_dir)

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
    print("URL: %s" % url)

    pr_list = []

    while True:
        resp = requests_url(url)
        pr_list = pr_list + resp.json()

        # Read next page
        if "next" not in resp.links:
            print("Read all pull requests: Total = %d" % len(pr_list))
            break

        print("Read next list")
        url = resp.links["next"]["url"]

    return pr_list

def get_dir_list(base_dir):
    """ Get the list of absolute path of directory """
    dir_list = []

    base_abs_dir = os.path.join(os.path.curdir, base_dir)

    print("Dir List: %s" % base_abs_dir)
    for item in sorted(os.listdir(base_abs_dir)):
        item_path = os.path.join(base_abs_dir, item)
        dir_list.append(os.path.abspath(item_path))
        print("   %s" % os.path.abspath(item_path))

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
    print("Patch File: %s" % patch_file)

    commit_msg = ""
    with open(patch_file, 'r') as pf:
        save_msg = False
        for line in pf:
            line = line.strip(" \t")
            if line == os.linesep and save_msg == False:
                print("Commit message start - first space")
                save_msg = True
                continue
            if re.search("---", line):
                print("Commit message end - \'---\'")
                break

            if save_msg == True:
                print("   commit msg: %s" % line)
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

    print("ENV: HUB_PROTOCOL: %s" % os.environ["HUB_PROTOCOL"])
    print("ENV: GITHUB_USER: %s" % os.environ["GITHUB_USER"])
    print("ENV: GITHUB_TOKEN: %s" % os.environ["GITHUB_TOKEN"])

    series_path_list = get_dir_list(series_path)

    src_dir = os.path.abspath(os.path.curdir)
    print("Current Src Dir: %s" % src_dir)

    # Get current pr from the target repo
    pr_list = github_get_pr_list(base_repo)

    for series_path in series_path_list:
        print("\n>> Series Path: %s" % series_path)

        # Read series json file
        json_file = os.path.join(series_path, "series.json")
        if not os.path.exists(json_file):
            print("ERROR: cannot find series detail: %s" % json_file)
            continue

        # Load series detail from series.json file
        with open(json_file, 'r') as jf:
            series = json.load(jf)
        print("Series id: %d" % series["id"])

        branch = str(series["id"])

        # Check PR list if it already exist
        if search_series_in_pr_list(pr_list, series["id"]):
            print("PR already exist. Skip creating PR")
            continue

        # Get list of patches from the pathces directory in series directory
        patch_path_list = get_dir_list(os.path.join(series_path, "patches"))
        if len(patch_path_list) == 0:
            print("ERROR: no patch file found from %s" % series_path)
            continue

        # create branch with series name
        git_checkout(src_dir, branch, create=True)

        # Apply patches
        if git_am(src_dir, patch_path_list) != 0:
            print("ERROR: Failed to apply patch.")
            git_checkout(src_dir, WORKING_BRANCH)
            # TODO: send email to the submitter and reqeust to send after rebase
            continue

        try:
            git_push(src_dir, branch)
        except subprocess.CalledProcessError as e:
            print("ERROR: Failed to push %s error=%d " % (branch, e.returncode))
            git_checkout(src_dir, WORKING_BRANCH)
            continue

        # Prepare PR message if cover_letter exist
        pr_msg = generate_pr_msg(series, series_path, patch_path_list)

        time.sleep(1)

        # use hub to create pr
        try:
            hub_create_pr(src_dir, pr_msg, base_repo, base_branch, branch)
        except subprocess.CalledProcessError as e:
            print("ERROR: failed to create pr error=%d" % e.returncode)
            git_push(src_dir, branch, delete=True)

        # Check out to the target_branch
        git_checkout(src_dir, WORKING_BRANCH)

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

def main():
    args = parse_args()

    create_pr_with_series(args.series_path,
                          args.base_repo,
                          args.base_branch)

if __name__ == "__main__":
    main()
