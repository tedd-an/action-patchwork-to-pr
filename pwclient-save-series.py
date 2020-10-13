#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import cgi
import json
import requests
import argparse
import re
from enum import Enum

# PatchWork REST API Base URL.
PW_URL_API_BASE=None

def requests_url(url):
    """ Helper function to requests GET with URL """
    resp = requests.get(url)
    if resp.status_code != 200:
        raise requests.HTTPError("GET {}".format(resp.status_code))

    return resp

def pw_get_project_id(name):
    """ Seach project list and get project id"""

    url = '{}/projects/'.format(PW_URL_API_BASE)

    projects = []

    while True:
        resp = requests.get(url)
        if resp.status_code != 200:
            raise requests.HTTPError("GET {}".format(resp.status_code))

        projects += resp.json()

        if "next" not in resp.links:
            print("Read all patches: %d" % len(projects))
            break

        url = resp.links["next"]["url"]

    print("Number of projects: %d" % len(projects))

    # Seach name from the project list
    for prj in projects:
        if prj["link_name"] == name:
            print("Found \"%s\" ID: %s" % (name, prj["id"]))
            return prj["id"]

    return None

def pw_get_patches(project_id, state):
    """ Get array of patches with given condition"""

    patches = []

    # First URL
    url = '{}/patches/?project={}&state={}&archived=0'.format(PW_URL_API_BASE,
                                                   project_id,
                                                   state)

    while True:
        resp = requests_url(url)
        patches = patches + resp.json()
        # Read next page
        if "next" not in resp.links:
            print("Read all patches: Total = %d" % len(patches))
            break

        print("Read next list")
        url = resp.links["next"]["url"]

    return patches

def id_exist(list, id):
    """ Check if id exist in list. List item should have "id" field """
    for item in list:
        if "id" in item and item["id"] == id:
            return True
    return False

def get_series_from_patches(patches):
    """
    This function exams the patch in the patch list to get the series id and
    add series to the series list if it doens't exist.
    """

    series_list =[]

    for patch in patches:
        # Skip if "series" not exist
        if "series" not in patch:
            continue

        for series in patch["series"]:
            # Check if series.id in the list
            if id_exist(series_list, series["id"]) == False:
                print("Add series %d to series list" % series["id"])
                series_list.append(series)

    return series_list

def get_filename(header):
    """ Get filename from resp header object """
    if "Content-Disposition" in header:
        value, param = cgi.parse_header(header["Content-Disposition"])
        if value == "attachment" and "filename" in param:
            return param["filename"]
    return None

def save_patches(patches, base_path):
    """ Save patches to the target path """
    for i, patch in enumerate(patches):
        resp = requests_url(patch["mbox"])
        filename = get_filename(resp.headers)
        if filename == None:
            filename = '{}-{}'.format(i + 1, patch["id"])
        dest_file = os.path.join(base_path, filename)
        print("Saving patch to %s" % dest_file)
        with open(dest_file, 'wb') as f:
            f.write(resp.content)

def save_cover_letter(cover_letter, base_path):
    """ Save cover letter to the folder """
    resp = requests_url(cover_letter["mbox"])
    dest_file = os.path.join(base_path, "cover_letter")
    print("Saving cover letter to %s" % dest_file)
    with open(dest_file, 'wb') as f:
        f.write(resp.content)

def save_series(url, project_name, patch_state, dest_path, exclude_str=None,
                include_str=None):
    """
    Save the various infomation of series to the folder named with series_id.
    It saves series.json, cover_letter, and pathces
    """

    global PW_URL_API_BASE

    PW_URL_API_BASE = url
    print("PatchWork REST API Base URL: %s" % PW_URL_API_BASE)

    project_id = pw_get_project_id(project_name)
    if not project_id:
        print("Unable to find the project name: %s" % project_name)
        return

    print("Project \"%s\" ID = %s" % (project_name, project_id))

    patches = []
    for state in patch_state:
        patches += pw_get_patches(project_id, state)
    print("Total number of patches: %d" % len(patches))

    series = get_series_from_patches(patches)
    print("Series list = %d" % len(series))

    save_path = os.path.abspath(dest_path)
    if not os.path.exists(save_path):
        os.mkdir(save_path)

    for item in series:
        if item["name"] == None:
            item["name"] = "Untitled series of #{}".format(item["id"])

        print("Series Name: %s" % item["name"])

        if exclude_str != None:
            if re.search(exclude_str, item["name"], re.IGNORECASE):
                print("Skip saving series. contains exlucde string")
                continue

        if include_str != None:
            if not re.search(include_str, item["name"], re.IGNORECASE):
                print("Skip saving series. does not contains include string")
                continue

        # Get series detail
        resp = requests_url(item["url"])

        series_detail = resp.json()

        print("Process series %d" % series_detail["id"])

        # Create series_id folder
        series_path = os.path.join(save_path, "%d" % series_detail["id"])
        if not os.path.exists(series_path):
            os.mkdir(series_path)

        # series details to json file
        json_file = os.path.join(series_path, "series.json")
        with open(json_file, 'w') as f:
            print("Saving series(%d) to %s" % (series_detail["id"], json_file))
            json.dump(series_detail, f)

        # Save cover letter if exist
        if series_detail["cover_letter"] != None:
            save_cover_letter(series_detail["cover_letter"], series_path)

        # Save patches
        if "patches" in series_detail:
            # Save patches under 'patches' folder
            patches_path = os.path.join(series_path, "patches")
            if not os.path.exists(patches_path):
                os.mkdir(patches_path)
            save_patches(series_detail["patches"], patches_path)

    print("Series patches are saved to: %s" % save_path)

def parse_args():
    """ Parse input argument """
    ap = argparse.ArgumentParser(
        description="PatchWork client that saves the patches from the series"
    )

    ap.add_argument("-u", "--url",
                    default="https://patchwork.kernel.org/api/1.1",
                    help="URL of PatchWork server to access. Default is "
                         "\'https://patchwork.kernel.org/api/1.1\'")

    ap.add_argument("-p", "--project-name", default="bluetooth",
                    help="Name of the project to use. Default is \'bluetooth\'")

    ap.add_argument("-s", "--patch-state", nargs='+', default=['1', '2'],
                    help="State of patch to query. Default is \'1\' and \'2\'")

    ap.add_argument("-d", "--dest-path", default="./series",
                    help="Path to save the patches. Default is \'.\\series\'")

    group = ap.add_mutually_exclusive_group()
    group.add_argument("-e", "--exclude-str", default="Bluetooth:",
                       help="Specify the string in the title to be execluded "
                            "from the list. Default is \'Bluetooth:\'")
    group.add_argument("-i", "--include-str",
                       help="Specify the string in the series title to be "
                            "included from the list")

    args = ap.parse_args()

    return args

def main():
    args = parse_args()

    save_series(args.url,
                args.project_name,
                args.patch_state,
                args.dest_path,
                args.exclude_str,
                args.include_str)

if __name__ == "__main__":
    main()
