from twitchAPI.pubsub import PubSub
from twitchAPI.twitch import Twitch
from twitchAPI.types import AuthScope, InvalidRefreshTokenException, CustomRewardRedemptionStatus
from twitchAPI.oauth import UserAuthenticator, refresh_access_token
from uuid import UUID

import traceback

import re
import json
import os

import yaml
import aiohttp

import irsdk
import time
import random
from threading import Thread

import logging

SCRIPTNAME = "iRTCPR"
VERSION = "0.10"
CONFIG_FILE = "config.yaml"
secrets_fn = "twitch_secrets.json"
redeem_cam_file = "redeem_cam.txt"
redeem_user_file = "redeem_user.txt"
redeems = []
DEBUG = False

# initate everything we need for thread safe logging to stdout
logger = logging.getLogger(SCRIPTNAME)
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
# create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# add formatter to ch
ch.setFormatter(formatter)
logger.addHandler(ch)

logger.info("---------------------------------------------")
logger.info("%s" % (SCRIPTNAME, ))
logger.info("Version: %s" % (VERSION))
logger.info("---------------------------------------------")

file_list = os.listdir()
if CONFIG_FILE not in file_list:
    logger.info("There is no config.yaml config file.")
    logger.info("Please copy the config_example.yaml to config.yaml")
    logger.info("and edit it.")
    logger.info("--------------------------------------------------")
    input("Press return key to end!")
    exit(0)
else:
    with open("config.yaml") as fl:
        config = yaml.load(fl, Loader=yaml.FullLoader)

CLIENT_ID = config["CLIENT_ID"]
CLIENT_SECRET = config["CLIENT_SECRET"]
USERNAME = config["USERNAME"]

twitch_secrets = {
    "TOKEN": None,
    "REFRESH_TOKEN": None,
}

# config reading class
class Config:

    def __init__(self, cf):
        self.reset()

    def reset(self):
        # let's forget everything we know and (re)load the config


# this is our State class, with some helpful variables
class State:

    def __init__(self):
        self.reset()

    def reset(self):
        # set all members to their initial value
        self.ir_connected = False
        self.last_car_setup_tick = -1
        self.SEARCH_FOR_DRIVER = True
        self.SEARCH_FOR_TEAM = True
        self.IS_TEAM_SESSION = False
        self.DEFAULT_CAMERA = None
        self.REDEEM_IS_ACTIVE = False
        self.last_epoch = -1
        self.prev_epoch = -1
        self.camswitch_epoch = -1
        self.prev_pctspecon = 0
        self.all_drivers_dict = {}
        self.all_teams_dict = {}
        self.track_length = 0
        self.currentCamera = "TV1"
        self.CAMERAS = {}
        self.RELOAD_CAMERAS = 0
        self.RELOAD_DRIVERS = 0
        self.SESSIONID = 0
        self.SUBSESSIONID = 0
        self.SESSIONNAME = "NO_SESSION"
        self.SESSIONNUM = 0
        self.DRIVER_DICT = {}
        self.DRIVER_LIST = []
        self.DRIVERTOSPECID = 0
        self.TEAMTOSPECID = 0
        self.CARTOSPECNUMBER = -1
        global TWITCH_REWARDS
        global SCRIPTNAME
        global USERNAME
        global TWITCHUSERID
        self.user_friends = False
        self.user_friend_dict = {}
        self.user_friend_insession = []
        self.team_friends = False
        self.team_friend_dict = {}
        self.team_friend_insession = []


def irtcprMain(r, stop):
    logger.info("Main - Thread starts")
    while not stop():
        if not state.ir_connected:
            try:
                ir.startup()
            except Exception as e:
                logger.critical("Main - cannot startup IRSDK: %s" % (e,))
                exit(1)
        time.sleep(1)
    logger.info("Main - Thread ends")


# initialize our State class
state = State()
# state.reset()

try:
    ir = irsdk.IRSDK(parse_yaml_async=True)
except Exception as e:
    logger.critical("cannot initialize IRSDK: %s" % (e,))


stop_main_thread = False

# first we need to open the main thread, which will
# check if we can connect to the iracing api
irtcprMainThread = Thread(target=irtcprMain, args=(redeems, lambda: stop_main_thread, ))
irtcprMainThread.start()
input("any key to end\n")
stop_main_thread = True
irtcprMainThread.join()

