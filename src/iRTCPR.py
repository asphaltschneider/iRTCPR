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
VERSION = "0.05"
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

# iracing related variables
CAMERAS = {}
RELOAD_CAMERAS = 1
RELOAD_DRIVERS = 0
SESSIONNUM = 1
SESSIONID = 1
SUBSESSIONID = 0
SESSIONNAME = 'None'
DRIVER_DICT = {}
DRIVER_LIST = []
DRIVERTOSPECID = -1
#DRIVERTOSPECNUMBER = -1
TWITCH_REWARDS = {}
TWITCHUSERID = -1
all_drivers_dict = {}

# this is our State class, with some helpful variables
class State:
    ir_connected = False
    last_car_setup_tick = -1
    SEARCH_FOR_DRIVER = True
    SEARCH_FOR_TEAM = True
    IS_TEAM_SESSION = False
    DEFAULT_CAMERA = None
    REDEEM_IS_ACTIVE = False
    last_epoch = -1
    prev_epoch = -1
    camswitch_epoch = -1
    prev_pctspecon = 0
    all_drivers_dict = {}
    all_teams_dict = {}
    track_length = 0
    currentCamera = "TV1"
    global CAMERAS
    global RELOAD_CAMERAS
    global RELOAD_DRIVERS
    SESSIONID = 0
    SUBSESSIONID = 0
    SESSIONNAME = "NO_SESSION"
    SESSIONNUM = 0
    global DRIVER_DICT
    global DRIVER_LIST
    global DRIVERTOSPECID
    global TEAMTOSPECID
    CARTOSPECNUMBER = -1
    global TWITCH_REWARDS
    global SCRIPTNAME
    global USERNAME
    global TWITCHUSERID
    user_friends = False
    user_friend_dict = {}
    user_friend_insession = []
    team_friends = False
    team_friend_dict = {}
    team_friend_insession = []


def update_twitch_secrets(new_data):
    with open(secrets_fn, "w+") as fl:
        fl.write(json.dumps(new_data))


def update_cam_file(camname):
    with open(redeem_cam_file, "w+") as fl:
        fl.write(camname)


def update_username_file(twitch_user):
    with open(redeem_user_file, "w+") as fl:
        fl.write(twitch_user)


def load_twitch_secrets():
    with open(secrets_fn) as fl:
        return json.loads(fl.read())


def callback(uuid: UUID, data: dict) -> None:
    try:
        if data["type"] != "reward-redeemed":
            return

        resp_data = data["data"]["redemption"]
        initiating_user = resp_data["user"]["login"]
        reward_broadcaster_id = resp_data["channel_id"]
        reward_id = resp_data["reward"]["id"]
        reward_prompt = resp_data["reward"]["prompt"]
        redemption_id = resp_data["id"]

        if SCRIPTNAME in reward_prompt:
            logger.info("TWITCH - User %s redeemed %s for %s seconds"
                        % (initiating_user, resp_data["reward"]["title"], config["CAMERA_SWITCH_TIME"]))

            tmpDict = {}
            tmpDict["reward_broadcaster_id"] = reward_broadcaster_id
            tmpDict["username"] = initiating_user
            tmpDict["reward_id"] = reward_id
            tmpDict["redemption_id"] = redemption_id
            tmpDict["title"] = resp_data["reward"]["title"]

            redeems.append(tmpDict)
        else:
            logger.info("TWITCH - User %s redeemed %s but it's not interesting for us."
                        % (initiating_user, resp_data["reward"]["title"], ))


    except Exception as e:
        logger.critical("".join(traceback.TracebackException.from_exception(e).format()))
        pass


async def callback_task(payload):
    try:
        if DEBUG:
            logger.debug("Running callback task...")

        if not twitch.session:
            twitch.session = aiohttp.ClientSession()

        logger.info("callback task")

    except Exception as e:
        logger.critical("".join(traceback.TracebackException.from_exception(e).format()))
        pass


def redeemCamSwitch(tmpRedeem):
    state.REDEEM_IS_ACTIVE = True
    logger.info("Internal - Switching redeemed cam to %s"
                % (tmpRedeem["title"],))
    update_cam_file(tmpRedeem["title"])
    update_username_file(tmpRedeem["username"])
    if tmpRedeem["title"] == config["REWARD_TITLE_RANDOMCAM"]:
        randomCamTitle = "TV1"
        while randomCamTitle == "TV1" or randomCamTitle == "Scenic":
            randomCamTitle = random.choice(list(state.CAMERAS))
        logger.info("Internal - the random cam will be %s"
                    % (randomCamTitle,))
        update_cam_file(randomCamTitle)
        switch_camera(state.CARTOSPECNUMBER, randomCamTitle)
    else:
        update_cam_file(tmpRedeem["title"])
        switch_camera(state.CARTOSPECNUMBER, tmpRedeem["title"])

    logger.info("Internal - locking cam for %s seconds"
                % (config["CAMERA_SWITCH_TIME"],))
    time.sleep(config["CAMERA_SWITCH_TIME"])
    logger.info("Internal - redeem time %s seconds is over"
                % (config["CAMERA_SWITCH_TIME"],))
    update_cam_file("")
    update_username_file("")
    state.REDEEM_IS_ACTIVE = False


def updateRedeemStatus(tmpRedeem, status):
    # trying to update the redeem
    try:
        tmp_redeem_list = [tmpRedeem["redemption_id"], ]
        twitch.update_redemption_status(broadcaster_id=str(state.TWITCHUSERID),
                                        reward_id=tmpRedeem["reward_id"],
                                        redemption_ids=tmp_redeem_list,
                                        status=status)
        return True
    except Exception as e:
        logger.critical("TWITCH - Something went wrong while updating the redeem status: %s" % (e, ))
        return False

def redeemFulfiller(r, stop):
    while True:
        if (len(r) > 0):
            tmpRedeem = r.pop()
            logger.info("Internal - User %s redeemed %s for %s seconds"
                        % (tmpRedeem["username"], tmpRedeem["title"], config["CAMERA_SWITCH_TIME"], ))

            redeemCamSwitch(tmpRedeem)
            updateRedeemStatus(tmpRedeem, CustomRewardRedemptionStatus.FULFILLED)
            #time.sleep(int(config["CAMERA_SWITCH_TIME"]))
            logger.info("Internal - Done processing... %s - %s" % (tmpRedeem["username"], tmpRedeem["title"], ))
        time.sleep(1)
        if stop():
            break


def redeemListInfo(r, stop):
    a = -1
    while True:
        if not len(r) == a:
            logger.info("Internal - currently waiting redeems %s" % (len(r), ))
            a = len(r)
        time.sleep(1)
        if stop():
            break


# here we check if we are connected to iracing
# so we can retrieve some data
def check_iracing():
    if state.ir_connected and not (ir.is_initialized and ir.is_connected):
        state.ir_connected = False
        # don't forget to reset your State variables
        state.last_car_setup_tick = -1
        # we are shutting down ir library (clearing all internal variables)
        ir.shutdown()
        logger.info('iRacing - irsdk disconnected')
        removerewards()
        state.SEARCH_FOR_DRIVER = True
        state.RELOAD_CAMERAS = 1
        state.RELOAD_DRIVERS = 0
        state.CAMERAS = {}
        state.DRIVER_DICT = {}
        state.DRIVER_LIST = []
        state.SUBSESSIONID = -1
        state.SESSIONID = -1
        state.SESSIONNUM = -1
        state.user_friends = False
        state.user_friend_dict = {}
        state.user_friend_insession = []
        state.team_friends = False
        state.team_friend_dict = {}
        state.team_friend_insession = []
        state.SESSIONNAME = "NO_SESSION"
        state.SESSIONNUM = 0
    elif not state.ir_connected and ir.startup() and ir.is_initialized and ir.is_connected:
        state.ir_connected = True
        logger.info('iRacing - irsdk connected')
        if ir['WeekendInfo']:
            tracklength = ir["WeekendInfo"]["TrackLength"]
            try:
                tracklength_km = re.search('([\d\.]+?)\ km.*$', tracklength).group(1)
            except AttributeError:
                tracklength_km = ''
            state.track_length = float(tracklength_km) * 1000
            logger.info('iRacing - The next events Category: %s'
                  % (ir['WeekendInfo']['Category'], ))
            logger.info("iRacing - Track: %s, %s, %s"
                  % (ir['WeekendInfo']['TrackDisplayName'],
                  ir['WeekendInfo']['TrackCity'],
                  ir['WeekendInfo']['TrackCountry']))
            logger.info("iRacing - SessionID: %s, SubSessionID: %s"
                        % (ir['WeekendInfo']['SessionID'],
                           ir['WeekendInfo']['SubSessionID'], ))
            state.SESSIONID = ir['WeekendInfo']['SessionID']
            state.SUBSESSIONID = ir['WeekendInfo']['SubSessionID']
            state.SESSIONNUM = ir["SessionNum"]
            if ir['WeekendInfo']['WeekendOptions']['StandingStart'] == 1:
                standingstart = "standing"
            else:
                standingstart = "rolling"
            logger.info("iRacing - Start type is %s start" % (standingstart, ))


def cameras():
    logger.info("iRacing - reading cameras")
    state.CAMERAS = {}

    if ir["CameraInfo"]:
        logger.debug("cameras - found CameraInfo")
        if ir["CameraInfo"]["Groups"]:
            logger.debug("cameras - found CameraInfo -> Groups")
            for c in ir["CameraInfo"]["Groups"]:
                state.CAMERAS[c["GroupName"]] = c["GroupNum"]
                logger.info("cameras - setting %s - %s" % (c["GroupName"], c["GroupNum"], ))
    logger.info("iRacing - finished reading cameras")


def switch_camera(number, cam):
    # logger.info("iRacing - Switching Camera to %s" % (cam, ))
    ir.cam_switch_num(number, state.CAMERAS[cam], 0)
    if not cam == state.currentCamera:
        logger.info("switch_camera - switching cam to %s" % (cam, ))
        state.currentCamera = cam


def findteam(uid):
    found_driver = False
    if ir["DriverInfo"]:
        for i in range(len(ir["DriverInfo"]["Drivers"])):
            if ir["DriverInfo"]["Drivers"][i]["TeamID"] == int(uid):
                state.CARTOSPECNUMBER = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                state.TEAMTOSPECID = i
                logger.info("iRacing - spotting Team #%s - %s"
                            % (state.CARTOSPECNUMBER,
                               ir["DriverInfo"]["Drivers"][i]["TeamName"]))
                switch_camera(state.CARTOSPECNUMBER, "Rear Chase")
                time.sleep(2)
                switch_camera(state.CARTOSPECNUMBER, state.DEFAULT_CAMERA)
                found_driver = True
                return False
    else:
        return True

    if not found_driver:
        logger.info("was not able to find the user %s in the current session" % (uid, ))


def finddriver(uid):
    found_driver = False
    if ir["DriverInfo"]:
        for i in range(len(ir["DriverInfo"]["Drivers"])):
            if ir["DriverInfo"]["Drivers"][i]["UserID"] == int(uid):
                state.CARTOSPECNUMBER = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                switch_camera(state.CARTOSPECNUMBER, "Rear Chase")
                time.sleep(2)
                switch_camera(state.CARTOSPECNUMBER, state.DEFAULT_CAMERA)
                found_driver = True
                state.DRIVERTOSPECID = i
                logger.info("iRacing - spotting Driver #%s - %s"
                            % (state.CARTOSPECNUMBER,
                               ir["DriverInfo"]["Drivers"][i]["UserName"]))
                return False
    else:
        return True

    if not found_driver:
        logger.info("was not able to find the user %s in the current session" % (uid, ))


def removerewards():
    all_existing_rewards = twitch.get_custom_reward(broadcaster_id=str(state.TWITCHUSERID))
    for k in all_existing_rewards["data"]:
        if SCRIPTNAME in k["prompt"]:
            twitch.delete_custom_reward(broadcaster_id=str(state.TWITCHUSERID), reward_id=k["id"])
            logger.info('TWITCH - removing reward %s' % (k["title"], ))


def createreward(user_id, i, tmpReward):
    reward_created = 0
    try:
        createdreward = twitch.create_custom_reward(broadcaster_id=user_id,
                                                    title=i,
                                                    prompt=tmpReward["prompt"],
                                                    cost=tmpReward["cost"],
                                                    global_cooldown_seconds=tmpReward["global_cooldown_seconds"],
                                                    is_global_cooldown_enabled=tmpReward["is_global_cooldown_enabled"])
        logger.info('TWITCH - setting up reward %s' % (i, ))
        if DEBUG:
            print(createdreward)
        reward_created = 1
    except Exception as e:
        if DEBUG:
            logger.info("TWITCH - cannot create reward %s" % (i, ))
            logger.info("TWITCH - Exception is %s" % (e, ))

    if reward_created == 0:
        if DEBUG:
            logger.info("TWITCH - check if reward is still there")

    state.TWITCH_REWARDS = twitch.get_custom_reward(broadcaster_id=user_id)


def autocamswitcher():
    switch_cam = 0
    DRIVER_LIST = []
    epoch_time = time.time()
    if ir["DriverInfo"]:
        if not state.IS_TEAM_SESSION:
            spec_on = state.DRIVERTOSPECID
        else:
            spec_on = state.TEAMTOSPECID
        pctspecon = ir["CarIdxLapDistPct"][spec_on]
        #print("tracklength", ir["WeekendInfo"]["TrackLength"])
        tracklength = ir["WeekendInfo"]["TrackLength"]
        try:
            tracklength_km = re.search('([\d\.]+?)\ km.*$', tracklength).group(1)
        except AttributeError:
            tracklength_km = ''
        tracklength_m = float(tracklength_km) * 1000
        #print("tracklength", tracklength_m)
        cur_pctspecon = pctspecon
        if state.prev_pctspecon > 0.8 and cur_pctspecon < 0.2:
            cur_pctspecon += 1
            #print("over the line")
        cur_m_specon = tracklength_m * cur_pctspecon
        prev_m_specon = tracklength_m * state.prev_pctspecon
        diff_m_specon = cur_m_specon - prev_m_specon
        diff_epoch = epoch_time - state.prev_epoch
        calc_speed = diff_m_specon / diff_epoch
        calc_speed_kmh = int((calc_speed * 3600) / 1000)
        #print(f"\033[2J")
        #print("\r", Fore.RED, "travelled", diff_m_specon, "meters in", diff_epoch, "seconds - speed", calc_speed_kmh, "km/h", end="")


        # Method 1: Easy but not very precise way to calculate gaps
        # * Calculate distance fraction between 2 cars by substracting their CarIdxLapDistPct
        # * Multiply by track length to get distance in m
        # * Divide by speed to get time gap
        DRIVER_LIST = []
        driversindistance = 0
        for i in range(len(ir["CarIdxLapDistPct"])):
            #print("i=%i" % (i))
            if i != int(spec_on) and i != 0:
                if ir["CarIdxLapDistPct"][i] != -1:
                    try:
                        pctdriver=(ir["CarIdxLapDistPct"][i] - pctspecon) * tracklength_m / calc_speed
                    except ZeroDivisionError:
                        pctdriver = (ir["CarIdxLapDistPct"][i] - pctspecon) * tracklength_m / 0.1
                    #if pctdriver < 0:
                    #    pctdriver += 100
                    #print("   ", i, pctdriver)
                    #print("Number", ir["DriverInfo"]["Drivers"][i]["CarNumber"], "UID",
                    #      ir["DriverInfo"]["Drivers"][i]["UserID"], ", Driver",
                    #      ir["DriverInfo"]["Drivers"][i]["UserName"])
                    if pctdriver <= (tracklength_m + 15):
                        tmpDict = {}
                        tmpDict["ID"] = i
                        tmpDict["DriverName"] = ir["DriverInfo"]["Drivers"][i]["UserName"]
                        tmpDict["UserID"] = ir["DriverInfo"]["Drivers"][i]["UserID"]
                        tmpDict["CarNumber"] = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                        tmpDict["Distance"] = pctdriver
                        DRIVER_LIST.append(tmpDict)
                    if pctdriver < 0.6 and pctdriver > -0.1:
                        if ir["DriverInfo"]["Drivers"][i]["CarNumber"] != state.CARTOSPECNUMBER:
                            driversindistance += 1
                            switch_cam = 1
                            switch_cam_number = state.CARTOSPECNUMBER
                            #print("opponent", ir["DriverInfo"]["Drivers"][i]["CarNumber"], "is infront     ", pctdriver, "driversindistance:", driversindistance)
                    elif pctdriver > -0.4 and pctdriver <= -0.1:
                        driversindistance += 1
                        switch_cam = 2
                        switch_cam_number = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                        #rint("opponent", switch_cam_number, "is close behind", pctdriver, "driversindistance:", driversindistance)
            elif ir["DriverInfo"]["Drivers"][i]["CarNumber"] != state.CARTOSPECNUMBER:
                if ir["DriverInfo"]["Drivers"][i]["CarNumber"] != "0":
                    tmpDict = {}
                    tmpDict["ID"] = i
                    tmpDict["DriverName"] = ir["DriverInfo"]["Drivers"][i]["UserName"]
                    tmpDict["UserID"] = ir["DriverInfo"]["Drivers"][i]["UserID"]
                    tmpDict["CarNumber"] = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                    tmpDict["Distance"] = 0
                    DRIVER_LIST.append(tmpDict)


        relative_dict = sorted(DRIVER_LIST, key=lambda x: x['Distance'], reverse=True)

        if SESSIONNAME != 'QUALIFY':
            if calc_speed_kmh > 60:
                if epoch_time - state.camswitch_epoch > 5:
                    if switch_cam == 1 and driversindistance == 1:
                        #print("1,1 Switch Cam to", DRIVERTOSPECNUMBER, "Chase")
                        #ir.cam_switch_num(carNum, 19, 0)
                        if not state.REDEEM_IS_ACTIVE:
                            switch_camera(state.CARTOSPECNUMBER, "Chase")
                    elif switch_cam == 2 and driversindistance == 1:
                        #print("2,1 Switch Cam to", switch_cam_number, "Gyro")
                        #ir.cam_switch_num(carNum, 2, 0)
                        #ir.cam_switch_num(switch_cam_number, CAMERA_DICT["Gyro"], 0)
                        if not state.REDEEM_IS_ACTIVE:
                            switch_camera(switch_cam_number, "Gyro")
                    elif switch_cam > 0 and driversindistance > 1:
                        #print(">0,>1 Switch Cam to", DRIVERTOSPECNUMBER, "Far Chase")
                        #ir.cam_switch_num(carNum, 16, 0)
                        #ir.cam_switch_num(DRIVERTOSPECNUMBER, CAMERA_DICT["Far Chase"], 0)
                        if not state.REDEEM_IS_ACTIVE:
                            switch_camera(state.CARTOSPECNUMBER, "Far Chase")
                    else:
                        #print("e Switch Cam to", DRIVERTOSPECNUMBER, "TV1")
                        #ir.cam_switch_num(carNum, 10, 0)
                        #ir.cam_switch_num(DRIVERTOSPECNUMBER, CAMERA_DICT["TV1"], 0)
                        if not state.REDEEM_IS_ACTIVE:
                            switch_camera(state.CARTOSPECNUMBER, state.DEFAULT_CAMERA)
                    state.camswitch_epoch = epoch_time
                    state.prev_epoch = epoch_time
                    state.prev_pctspecon = pctspecon
                    # return epoch_time, epoch_time, pctspecon, DRIVERTOSPECNUMBER, SESSIONNAME
            else:
                if not state.REDEEM_IS_ACTIVE:
                    switch_camera(state.CARTOSPECNUMBER, "Chase")
                    state.camswitch_epoch = epoch_time
                    state.prev_epoch = epoch_time
                    state.prev_pctspecon = pctspecon

        else:
            #ir.cam_switch_num(DRIVERTOSPECNUMBER, CAMERA_DICT["Chase"], 0)
            if not state.REDEEM_IS_ACTIVE:
                switch_camera(state.CARTOSPECNUMBER, "Chase")
            state.camswitch_epoch = epoch_time
            state.prev_epoch = epoch_time
            state.prev_pctspecon = pctspecon
            # return epoch_time, epoch_time, pctspecon, DRIVERTOSPECNUMBER, SESSIONNAME

    state.camswitch_epoch = state.camswitch_epoch
    state.prev_epoch = epoch_time
    #state.prev_pctspecon = pctspecon
    # return last_epoch, epoch_time, pctspecon, DRIVERTOSPECNUMBER, SESSIONNAME


def DriverOrTeamsWorker(stop):
    logger.info("DriverOrTeamsWorker - Thread starts")
    this_all_drivers_dict = {}
    log_once_disconnected = 0
    log_once_connected = 0
    sessionnum = state.SESSIONNUM
    while not stop():
        # try:
            if state.ir_connected:
                if log_once_connected == 0:
                    logger.info("DriverOrTeamsWorker - connected to race server")
                    log_once_connected = 1
                    log_once_disconnected = 0

                try:
                    if ir["WeekendInfo"] and ir["DriverInfo"] and ir["SessionNum"]:
                        #logger.info("found WeekendInfo and DriverInfo")
                        # ir.freeze_var_buffer_latest()

                        if state.IS_TEAM_SESSION == False:
                            #logger.info("its not a team session")
                            for i in range(len(ir["DriverInfo"]["Drivers"])):
                                #logger.info("looping through %s" % (i,))
                                carIdx = ir["DriverInfo"]["Drivers"][i]["CarIdx"]
                                #logger.info("isspec %s" % (ir["DriverInfo"]["Drivers"][i]["IsSpectator"],))
                                #logger.info("userid %s" % (ir["DriverInfo"]["Drivers"][i]["UserID"],))
                                if not ir["DriverInfo"]["Drivers"][i]["UserID"] == -1 \
                                        and not carIdx in this_all_drivers_dict:
                                    if ir["DriverInfo"]["Drivers"][i]["IsSpectator"] == 0\
                                            and not ir["DriverInfo"]["Drivers"][i]["UserID"] == -1:
                                        #logger.info("carIdx %s" % (carIdx,))
                                        if ir["DriverInfo"]["Drivers"][i]["UserID"] in state.user_friend_dict:
                                            logger.info("DriverOrTeamsWorker - found a friend driver: %s" % (ir["DriverInfo"]["Drivers"][i]["UserName"],))
                                            state.user_friend_insession.append(ir["DriverInfo"]["Drivers"][i]["UserID"])
                                        try:
                                            tmpDict = {}
                                            tmpDict["CarNumber"] = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                                            tmpDict["UserName"] = ir["DriverInfo"]["Drivers"][i]["UserName"]
                                            tmpDict["UserID"] = ir["DriverInfo"]["Drivers"][i]["UserID"]
                                            tmpDict["CarScreenName"] = ir["DriverInfo"]["Drivers"][i]["CarScreenName"]
                                            tmpDict["IRating"] = ir["DriverInfo"]["Drivers"][i]["IRating"]
                                            tmpDict["LicString"] = ir["DriverInfo"]["Drivers"][i]["LicString"]
                                            tmpDict["PrevLapDistPct"] = ir["CarIdxLapDistPct"][i]
                                            tmpDict["PrevLapDistPctEpoch"] = time.time()
                                            tmpDict["LapDistPct"] = ir["CarIdxLapDistPct"][i]
                                            tmpDict["LapDistPctEpoch"] = time.time()
                                            this_all_drivers_dict[carIdx] = tmpDict
                                        except Exception as e:
                                            logger.critical("DriverOrTeamsWorker - something strange occured 598 %s" % (e,))
                                            pass
                                            time.sleep(1)
                                        print(this_all_drivers_dict[carIdx])

                            # next is position and speed
                            if ir["CarIdxLapDistPct"]:
                                for i in range(len(ir["DriverInfo"]["Drivers"])):
                                    if ir["DriverInfo"]["Drivers"][i]["IsSpectator"] == 0\
                                            and not ir["DriverInfo"]["Drivers"][i]["UserID"] == -1:
                                        #logger.info("carIdx %s" % (i,))
                                        if i in this_all_drivers_dict:
                                            tmpPosDict = {}
                                            tmpPosDict["CarNumber"] = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                                            tmpPosDict["UserName"] = ir["DriverInfo"]["Drivers"][i]["UserName"]
                                            tmpPosDict["UserID"] = ir["DriverInfo"]["Drivers"][i]["UserID"]
                                            tmpPosDict["CarScreenName"] = ir["DriverInfo"]["Drivers"][i]["CarScreenName"]
                                            tmpPosDict["IRating"] = ir["DriverInfo"]["Drivers"][i]["IRating"]
                                            tmpPosDict["LicString"] = ir["DriverInfo"]["Drivers"][i]["LicString"]
                                            tmpPosDict["PrevLapDistPct"] = this_all_drivers_dict[i]["LapDistPct"]
                                            tmpPosDict["PrevLapDistPctEpoch"] = this_all_drivers_dict[i]["LapDistPctEpoch"]
                                            tmpPosDict["LapDistPct"] = ir["CarIdxLapDistPct"][i]
                                            tmpPosDict["LapDistPctEpoch"] = time.time()
                                            # now we know how far the driver moved, so let's calculate his speed
                                            spd_pctlap = ir["CarIdxLapDistPct"][i]
                                            # if he crosses the line now, we need to virtually move him for the calculcation
                                            if tmpPosDict["PrevLapDistPct"] > 0.8 and spd_pctlap < 0.2:
                                                spd_pctlap += 1
                                            spd_meters = state.track_length * spd_pctlap
                                            spd_prev_meters = state.track_length * tmpPosDict["PrevLapDistPct"]
                                            spd_diff_meters = spd_meters - spd_prev_meters
                                            spd_diff_epoch = tmpPosDict["LapDistPctEpoch"] - tmpPosDict["PrevLapDistPctEpoch"]
                                            if spd_diff_epoch > 0:
                                                spd_calulated_ms = spd_diff_meters / spd_diff_epoch
                                            else:
                                                spd_calulated_ms = spd_diff_meters / 0.1
                                            spd_calulated_kmh = int((spd_calulated_ms * 3600) / 1000)
                                            tmpPosDict["Speed_ms"] = spd_calulated_ms
                                            tmpPosDict["Speed_kmh"] = spd_calulated_kmh
                                            this_all_drivers_dict.pop(i, None)
                                            this_all_drivers_dict[i] = tmpPosDict
                                            #if int(state.CARTOSPECNUMBER) > -1:
                                            #    if ir["DriverInfo"]["Drivers"][i]["CarNumber"] == state.CARTOSPECNUMBER \
                                            #            or ir["DriverInfo"]["Drivers"][i]["UserID"] in state.user_friend_dict:
                                            #        print(tmpPosDict)
                        else:
                            for i in range(len(ir["DriverInfo"]["Drivers"])):
                                #logger.info("looping through %s" % (i,))
                                carIdx = ir["DriverInfo"]["Drivers"][i]["CarIdx"]
                                logger.info("DriverOrTeamsWorker - carIdx %s" % (carIdx,))
                                #logger.info("isspec %s" % (ir["DriverInfo"]["Drivers"][i]["IsSpectator"],))
                                logger.info("TeamID %s" % (ir["DriverInfo"]["Drivers"][i]["TeamID"],))
                                if carIdx in this_all_drivers_dict:
                                    logger.info("carIdx %s in dict: %s %s" % (carIdx,
                                                                              this_all_drivers_dict[carIdx]["CarNumber"],
                                                                              ir["DriverInfo"]["Drivers"][carIdx]["TeamName"],))
                                if not ir["DriverInfo"]["Drivers"][i]["TeamID"] == 0 \
                                        and not carIdx in this_all_drivers_dict:
                                    logger.info("DriverOrTeamsWorker - carIdx %s" % (carIdx,))
                                    if ir["DriverInfo"]["Drivers"][i]["IsSpectator"] == 0\
                                            and not ir["DriverInfo"]["Drivers"][i]["TeamID"] == 0:
                                        logger.info("DriverOrTeamsWorker - carIdx %s" % (carIdx,))
                                        if ir["DriverInfo"]["Drivers"][i]["TeamID"] in state.team_friend_dict:
                                            logger.info("DriverOrTeamsWorker - found a friend team: %s" % (ir["DriverInfo"]["Drivers"][i]["TeamName"],))
                                            state.team_friend_insession.append(ir["DriverInfo"]["Drivers"][i]["TeamID"])
                                        try:
                                            tmpDict = {}
                                            tmpDict["CarNumber"] = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                                            tmpDict["UserName"] = ir["DriverInfo"]["Drivers"][i]["UserName"]
                                            tmpDict["UserID"] = ir["DriverInfo"]["Drivers"][i]["UserID"]
                                            tmpDict["TeamID"] = ir["DriverInfo"]["Drivers"][i]["TeamID"]
                                            tmpDict["CarScreenName"] = ir["DriverInfo"]["Drivers"][i]["CarScreenName"]
                                            tmpDict["IRating"] = ir["DriverInfo"]["Drivers"][i]["IRating"]
                                            tmpDict["LicString"] = ir["DriverInfo"]["Drivers"][i]["LicString"]
                                            tmpDict["PrevLapDistPct"] = ir["CarIdxLapDistPct"][i]
                                            tmpDict["PrevLapDistPctEpoch"] = time.time()
                                            tmpDict["LapDistPct"] = ir["CarIdxLapDistPct"][i]
                                            tmpDict["LapDistPctEpoch"] = time.time()
                                            this_all_drivers_dict[carIdx] = tmpDict
                                        except Exception as e:
                                            logger.critical("DriverOrTeamsWorker - something strange occured %s" % (e,))
                                            pass
                                            time.sleep(1)
                                        print(this_all_drivers_dict[carIdx])

                            # next is position and speed
                            if ir["CarIdxLapDistPct"]:
                                for i in range(len(ir["DriverInfo"]["Drivers"])):
                                    if ir["DriverInfo"]["Drivers"][i]["IsSpectator"] == 0\
                                            and not ir["DriverInfo"]["Drivers"][i]["TeamID"] == 0:
                                        #logger.info("carIdx %s" % (i,))
                                        if i in this_all_drivers_dict:
                                            tmpPosDict = {}
                                            tmpPosDict["CarNumber"] = ir["DriverInfo"]["Drivers"][i]["CarNumber"]
                                            tmpPosDict["UserName"] = ir["DriverInfo"]["Drivers"][i]["UserName"]
                                            tmpPosDict["UserID"] = ir["DriverInfo"]["Drivers"][i]["UserID"]
                                            tmpPosDict["TeamID"] = ir["DriverInfo"]["Drivers"][i]["TeamID"]
                                            tmpPosDict["CarScreenName"] = ir["DriverInfo"]["Drivers"][i]["CarScreenName"]
                                            tmpPosDict["IRating"] = ir["DriverInfo"]["Drivers"][i]["IRating"]
                                            tmpPosDict["LicString"] = ir["DriverInfo"]["Drivers"][i]["LicString"]
                                            tmpPosDict["PrevLapDistPct"] = this_all_drivers_dict[i]["LapDistPct"]
                                            tmpPosDict["PrevLapDistPctEpoch"] = this_all_drivers_dict[i]["LapDistPctEpoch"]
                                            tmpPosDict["LapDistPct"] = ir["CarIdxLapDistPct"][i]
                                            tmpPosDict["LapDistPctEpoch"] = time.time()
                                            # now we know how far the driver moved, so let's calculate his speed
                                            spd_pctlap = ir["CarIdxLapDistPct"][i]
                                            # if he crosses the line now, we need to virtually move him for the calculcation
                                            if tmpPosDict["PrevLapDistPct"] > 0.8 and spd_pctlap < 0.2:
                                                spd_pctlap += 1
                                            spd_meters = state.track_length * spd_pctlap
                                            spd_prev_meters = state.track_length * tmpPosDict["PrevLapDistPct"]
                                            spd_diff_meters = spd_meters - spd_prev_meters
                                            spd_diff_epoch = tmpPosDict["LapDistPctEpoch"] - tmpPosDict["PrevLapDistPctEpoch"]
                                            if spd_diff_epoch > 0:
                                                spd_calulated_ms = spd_diff_meters / spd_diff_epoch
                                            else:
                                                spd_calulated_ms = spd_diff_meters / 0.1
                                            spd_calulated_kmh = int((spd_calulated_ms * 3600) / 1000)
                                            tmpPosDict["Speed_ms"] = spd_calulated_ms
                                            tmpPosDict["Speed_kmh"] = spd_calulated_kmh
                                            this_all_drivers_dict.pop(i, None)
                                            this_all_drivers_dict[i] = tmpPosDict
                                            #if int(state.CARTOSPECNUMBER) > -1:
                                            #    if ir["DriverInfo"]["Drivers"][i]["CarNumber"] == state.CARTOSPECNUMBER \
                                            #            or ir["DriverInfo"]["Drivers"][i]["UserID"] in state.user_friend_dict:
                                            #        print(tmpPosDict)


                        #ir.unfreeze_var_buffer_latest()
                        time.sleep(1)
                except Exception as e:
                    logger.critical("DriverOrTeamsWorker - an exception occured %s" % (e,))
                    pass
                    time.sleep(1)

            else:
                this_all_drivers_dict = {}
                state.all_drivers_dict = this_all_drivers_dict
                state.track_length = 0
                if log_once_disconnected == 0:
                    logger.info("DriverOrTeamsWorker - disconnected from race server")
                    log_once_disconnected = 1
                    log_once_connected = 0


        # except Exception as e:
        #     logger.critical("DriverOrTeamsWorker - an exception occured %s" % (e, ))
    logger.info("DriverOrTeamsWorker - Thread ends")


def iRacingWorker(r, stop):
        logger.info("iRacingWorker - Thread starts")
        # who are we interested in?
        druid = config["IRACING_ID"]
        teamid = config["IRACING_TEAM_ID"]
        logger.info("iRacing - will watch out for Driver %s" % (druid, ))
        logger.info("iRacing - will watch out for Team %s" % (teamid, ))
        state.DRIVERTOSPECID = druid
        state.TEAMTOSPECID = teamid

        state.RELOAD_CAMERAS = 1
        # looping iracing

        while not stop():
            if not state.ir_connected:
                try:
                    ir.startup()
                except Exception as e:
                    logger.critical("cannot startup IRSDK: %s" % (e,))
                    exit(1)
            try:
                check_iracing()
            except Exception as e:
                logger.critical("iRacingWorker - Exception while checking iracing: %s" % (e,))
            # if we are, then process data
            if state.ir_connected and ir["WeekendInfo"] and ir["SessionInfo"] and ir["DriverInfo"]:
                ir.freeze_var_buffer_latest()
                # identify the current session, the driver is in
                if state.RELOAD_CAMERAS == 1:
                    logger.critical("iRacingWorker - calling cameras")
                    # cameras()
                    state.CAMERAS = {}
                    if ir["CameraInfo"]:
                        logger.debug("cameras - found CameraInfo")
                        if ir["CameraInfo"]["Groups"]:
                            logger.debug("cameras - found CameraInfo -> Groups")
                            for c in ir["CameraInfo"]["Groups"]:
                                state.CAMERAS[c["GroupName"]] = c["GroupNum"]
                                logger.info("cameras - setting %s - %s" % (c["GroupName"], c["GroupNum"],))
                    if len(state.CAMERAS) > 1:
                        state.RELOAD_CAMERAS = 0

                    if ir['WeekendInfo']:
                        if ir["SessionNum"] != state.SESSIONNUM\
                                or state.SESSIONID != ir["SessionID"]\
                                or state.SUBSESSIONID != ir['WeekendInfo']['SubSessionID']:
                            logger.info("Current Session is %s" % (state.SESSIONNAME,))
                            state.SESSIONID = ir['WeekendInfo']["SessionID"]
                            logger.info("sessionid %s" % (state.SESSIONID,))
                            state.SUBSESSIONID = ir['WeekendInfo']["SubSessionID"]
                            logger.info("SUBSESSIONID %s" % (state.SUBSESSIONID,))
                            SESSIONNUM = ir["SessionNum"]
                            state.SESSIONNAME = ir["SessionInfo"]["Sessions"][int(SESSIONNUM)]["SessionName"]
                            state.SESSIONNUM = ir["SessionNum"]
                            logger.info("SessionNum %s" % (SESSIONNUM,))
                            logger.info("Current Session is %s" % (state.SESSIONNAME, ))
                            if ir['WeekendInfo']:
                                if ir['WeekendInfo']["TeamRacing"] == 1:
                                    logger.info("teamracing is set")
                                    state.IS_TEAM_SESSION = True
                                    state.SEARCH_FOR_TEAM = True
                                    state.SEARCH_FOR_DRIVER = False
                                else:
                                    logger.info("teamracing is NOT set")
                                    state.IS_TEAM_SESSION = False
                                    state.SEARCH_FOR_TEAM = False
                                    state.SEARCH_FOR_DRIVER = True

                    logger.info("next will be the rewards")

                    if config["CAMERA_SWITCH_ENABLED"] and state.RELOAD_CAMERAS == 0:
                        for i in config["CAMERAS"]:
                            tmpReward = {}
                            tmpReward["title"] = i
                            tmpReward[
                                "prompt"] = "Schaltet die Kamera auf " + i + ". Automatisch erstellt durch " + SCRIPTNAME
                            tmpReward["cost"] = config["CAMERA_SWITCH_COST"]
                            tmpReward["is_global_cooldown_enabled"] = config["CAMERA_SWITCH_COOLDOWN_ENABLED"]
                            tmpReward["global_cooldown_seconds"] = config["CAMERA_SWITCH_COOLDOWN"]

                            createreward(user_id, i, tmpReward)
                        tmpReward["title"] = config["REWARD_TITLE_RANDOMCAM"]
                        tmpReward[
                            "prompt"] = "Schaltet die Kamera per Zufall. Automatisch erstellt durch " + SCRIPTNAME
                        tmpReward["cost"] = config["CAMERA_SWITCH_COST"]
                        tmpReward["is_global_cooldown_enabled"] = config["CAMERA_SWITCH_COOLDOWN_ENABLED"]
                        tmpReward["global_cooldown_seconds"] = config["CAMERA_SWITCH_COOLDOWN"]

                        createreward(user_id, "Zufall", tmpReward)
                    if config["FRIENDS_SWITCH_ENABLED"] and len(state.user_friend_insession) > 0:
                        print(state.user_friend_insession)
                        for i in state.user_friend_insession:
                            tmpReward = {}
                            tmpReward["title"] = state.user_friend_dict[i]
                            tmpReward[
                                "prompt"] = "Schaltet die Kamera auf den Fahrer %s. Automatisch erstellt durch %s" % (state.user_friend_dict[i], SCRIPTNAME,)
                            tmpReward["cost"] = config["FRIENDS_SWITCH_COST"]
                            tmpReward["is_global_cooldown_enabled"] = config["FRIENDS_SWITCH_COOLDOWN_ENABLED"]
                            tmpReward["global_cooldown_seconds"] = config["FRIENDS_SWITCH_COOLDOWN"]
                            createreward(user_id, state.user_friend_dict[i], tmpReward)
                        print(state.team_friend_insession)
                        for i in state.team_friend_insession:
                            tmpReward = {}
                            tmpReward["title"] = state.team_friend_dict[i]
                            tmpReward[
                                "prompt"] = "Schaltet die Kamera auf das Team %s. Automatisch erstellt durch %s" % (state.team_friend_dict[i], SCRIPTNAME,)
                            tmpReward["cost"] = config["FRIENDS_SWITCH_COST"]
                            tmpReward["is_global_cooldown_enabled"] = config["FRIENDS_SWITCH_COOLDOWN_ENABLED"]
                            tmpReward["global_cooldown_seconds"] = config["FRIENDS_SWITCH_COOLDOWN"]
                            createreward(user_id, state.team_friend_dict[i], tmpReward)


            if state.ir_connected:
                if state.SEARCH_FOR_DRIVER or state.SEARCH_FOR_TEAM:
                    if ir['WeekendInfo']:
                        if state.SEARCH_FOR_DRIVER:
                            try:
                                state.SEARCH_FOR_DRIVER = finddriver(druid)
                            except Exception as e:
                                logger.critical("iRacingWorker - calling finddriver caused an error %s" % (e,))
                        else:
                            state.SEARCH_FOR_TEAM = findteam(teamid)
                else:
                    autocamswitcher()

            ir.unfreeze_var_buffer_latest()
            time.sleep(1)

        logger.info("iRacingWorker - Thread ends")


# initialize our State class
state = State()
# initialize IRSDK
try:
    ir = irsdk.IRSDK(parse_yaml_async=True)
except Exception as e:
    logger.critical("cannot initialize IRSDK: %s" % (e,))

file_list = os.listdir()

if secrets_fn not in file_list:
    update_twitch_secrets(twitch_secrets)
else:
    twitch_secrets = load_twitch_secrets()

TOKEN = twitch_secrets["TOKEN"]
REFRESH_TOKEN = twitch_secrets["REFRESH_TOKEN"]
headers = {"content-type": "application/json"}

twitch = Twitch(CLIENT_ID, CLIENT_SECRET)
twitch.session = None

# setting up Authentication and getting your user id
twitch.authenticate_app([])

target_scope = [
    AuthScope.CHANNEL_READ_REDEMPTIONS,
    AuthScope.CHANNEL_MANAGE_REDEMPTIONS
]

auth = UserAuthenticator(twitch, target_scope, force_verify=True)

if (not TOKEN) or (not REFRESH_TOKEN):
    # this will open your default browser and prompt you with the twitch verification website
    TOKEN, REFRESH_TOKEN = auth.authenticate()
else:
    try:
        TOKEN, REFRESH_TOKEN = refresh_access_token(
            REFRESH_TOKEN, CLIENT_ID, CLIENT_SECRET
        )
    except InvalidRefreshTokenException:
        TOKEN, REFRESH_TOKEN = auth.authenticate()


twitch_secrets["TOKEN"] = TOKEN
twitch_secrets["REFRESH_TOKEN"] = REFRESH_TOKEN
update_twitch_secrets(twitch_secrets)

twitch.set_user_authentication(TOKEN, target_scope, REFRESH_TOKEN)

user_id = twitch.get_users(logins=[USERNAME])["data"][0]["id"]
state.TWITCHUSERID = user_id

state.DEFAULT_CAMERA = config["CAMERA_DEFAULT"]

# check if we should also watch for friends
if config["FRIENDS_SWITCH_ENABLED"] == True:
    state.user_friends = True
    tmp_dict = {}
    for i in config["FRIENDS"]["DRIVERS"]:
        print(i)
        print("id %s nickname %s" % (i, config["FRIENDS"]["DRIVERS"][i],))
        tmp_dict[i] = config["FRIENDS"]["DRIVERS"][i]
    state.user_friend_dict = tmp_dict
    tmp_dict = {}
    for i in config["FRIENDS"]["TEAMS"]:
        print(i)
        print("id %s team nickname %s" % (i, config["FRIENDS"]["TEAMS"][i],))
        tmp_dict[i] = config["FRIENDS"]["TEAMS"][i]
    state.team_friend_dict = tmp_dict


# starting up PubSub
pubsub = PubSub(twitch)
pubsub.start()

removerewards()

# you can either start listening before or after you started pubsub.
uuid = pubsub.listen_channel_points(user_id, callback)

stop_threads = False
redeemMonitorThread = Thread(target=redeemListInfo, args=(redeems, lambda: stop_threads, ))
redeemWorkThread = Thread(target=redeemFulfiller, args=(redeems, lambda: stop_threads, ))
iRacingThread = Thread(target=iRacingWorker, args=(redeems, lambda: stop_threads, ))
iRacingDriverThread = Thread(target=DriverOrTeamsWorker, args=(lambda: stop_threads, ))
redeemMonitorThread.start()
redeemWorkThread.start()
iRacingThread.start()
iRacingDriverThread.start()

input("any key to end\n")
stop_threads = True
redeemMonitorThread.join()
redeemWorkThread.join()
iRacingThread.join()
iRacingDriverThread.join()
pubsub.unlisten(uuid)
pubsub.stop()
