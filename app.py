import asyncio
import json
import logging
import os

import pandas as pd
import waitress
from flask import (
    Flask,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from sqlmodel import create_engine, Session

from email_data.send_email import prepare_email_html, send_email
from rightmove.geolocation import update_locations
from rightmove.models import sqlite_url
from rightmove.run import (
    download_properties,
    download_property_data,
    get_properties,
    mark_properties_reviewed,
)
from config import IS_WINDOWS, DATA

app = Flask(__name__)

logger = logging.getLogger("waitress")
logger.setLevel(logging.INFO)


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@app.route("/")
def index():
    engine = create_engine(sqlite_url, echo=False)

    # Get review dates:
    sql = "select distinct email_id, str_date from reviewdates order by email_id desc"
    items = pd.read_sql(sql, engine).to_records()

    new_properties = count_new_properties()

    return render_template(
        "index.html", title="Home", items=items, new_properties=new_properties
    )


@app.route("/email_template", methods=["GET"])
def email_template():
    data = request.args.to_dict()
    review_id = data.get("id")
    match review_id:
        case "latest":
            review_filter = "review_id is null"
        case _:
            review_filter = f"review_id = {review_id}"

    new_properties = count_new_properties()
    properties = get_properties(review_filter)

    return render_template(
        "template.html",
        title="View properties",
        properties=properties,
        review_id=review_id,
        new_properties=new_properties,
    )


@app.route("/review_latest")
def review_latest():
    mark_properties_reviewed()

    return redirect(url_for("index"))


@app.route("/download")
def download():
    asyncio.run(download_properties("BUY"))
    asyncio.run(download_property_data(update=False))
    update_locations()

    return redirect(url_for("index"))


@app.route("/send_email")
def send():
    data = request.args.to_dict()
    review_id = data.get("id")

    if prepare_email_html(review_id):
        send_email()

    return redirect(url_for("index"))


@app.route("/delete_review")
def delete_review():
    data = request.args.to_dict()
    review_id = data.get("id")

    engine = create_engine(sqlite_url, echo=False)
    with Session(engine) as session:
        date = session.exec(
            f"select reviewed_date from reviewdates where email_id={review_id}"
        ).first()[0]
        session.exec(f"delete from reviewdates where email_id={review_id}")
        session.exec(f"delete from reviewedproperties where reviewed_date='{date}'")
        session.commit()

    return redirect(url_for("index"))


@app.route("/settings", methods=["GET"])
def settings():
    with open(os.path.join(DATA, "email_details.json"), "r") as f:
        email_data = json.load(f)
    return render_template("settings.html", email_data=email_data)


@app.route("/settings", methods=["POST"])
def update_settings():
    form_data = request.form
    recipients = form_data.getlist("recipients[]")

    file = os.path.join(DATA, "email_details.json")
    with open(file, "r") as f:
        data = json.load(f)
        data["recipients"] = recipients

    with open(file, "w") as f:
        json.dump(data, f, indent=4)

    return redirect("/")


def count_new_properties() -> str:
    engine = create_engine(sqlite_url, echo=False)
    # Get count of new properties:
    sql = "select count(*) from alert_properties where travel_time < 45 and review_id is null"
    count_props = pd.read_sql(sql, engine).values[0][0]

    new_properties = ""
    if count_props > 0:
        new_properties = f" - {count_props} new"

    return new_properties


if __name__ == "__main__":
    if IS_WINDOWS:
        host = "127.0.0.1"
        port = 5002
    else:
        host = "0.0.0.0"
        port = 5001

    logger.info("Starting server...")
    waitress.serve(app, port=port, host=host)