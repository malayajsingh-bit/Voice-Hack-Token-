#!/usr/bin/env python3
"""Cohort CSV for a Sarvam campaign: one row per seller, columns become agent variables.

    python3 service/cohort.py 4213238:+919999999999 30220636:+918888888888 > data/cohort.csv
"""
import csv
import sys

import context

FIELDS = ["phone", "glid", "seller_md", "persona", "playbook", "avoid", "hook"]

if __name__ == "__main__":
    w = csv.DictWriter(sys.stdout, fieldnames=FIELDS)
    w.writeheader()
    for arg in sys.argv[1:]:
        glid, phone = arg.split(":", 1)
        v = context.variables(glid)
        w.writerow({"phone": phone, **{k: str(v.get(k, "")).replace("\n", " | ") for k in FIELDS if k != "phone"}})
