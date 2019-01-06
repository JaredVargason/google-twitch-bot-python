from gmusicapi import Mobileclient, Musicmanager
from datetime import datetime, timedelta
import irc.bot
import os.path
import requests
import time
import vlc
import random
import redis
from threading import Timer, Thread

from settings import (HOST, PORT, USERNAME, CLIENT_ID, 
    OAUTH_TOKEN, CHANNEL, ANDROID_DEVICE_ID, RADIO_STATIONS,
    SUPERUSERS)

#JARED TO DO FOR 1/2/2018
#TIDY UP THIS SCRIPT AND REQUIREMENTS FILES

class TimeUtils:
    epoch = datetime.utcfromtimestamp(0)

    @classmethod
    def sec_to_ms(cls, seconds):
        return seconds * 1000

    @classmethod
    def ms_to_sec(cls, ms):
        return ms / 1000.0

    @classmethod
    def datetime_to_ms(cls, dt):
        if type(dt) == int or type(dt) == float:
            return dt
        return (dt - TimeUtils.epoch).total_seconds() * 1000.0

    @classmethod
    def ms_difference(cls, dt1, dt2): 
        dt1_ms = cls.datetime_to_ms(dt1)
        dt2_ms = cls.datetime_to_ms(dt2)
        return abs(dt1_ms - dt2_ms)

class ElapsedTimer(Timer):
    def __init__(self, interval, function, *args, **kwargs):
        self.start_time = None
        Timer.__init__(self, interval, function, *args, **kwargs)
    
    def start(self):
        self.start_time = datetime.now()
        Timer.start(self)

    def elapsed(self):
        if self.start_time:
            #return (self.start_time - ElapsedTimer.epoch).total_seconds() * 1000.0
            #start_time_ms = TimeUtils.datetime_to_ms(self.start_time)
            return TimeUtils.ms_difference(self.start_time, datetime.now())

        return None

class Track:
    def __init__(self, name, track_id, duration=0):
        self.name = name
        self.track_id = track_id
        self.duration = duration

    def __str__(self):
        if self.name:
            return self.name
        
        else:
            return 'No info'

class RadioStation:
    def __init__(self, name, playlist):
        self.name = name
        self.last_turned_to = TimeUtils.datetime_to_ms(datetime.now())
        self.current_song_index = 0 
        self.last_track_time = 0

        self.tracks = self.randomize_tracks(self.get_tracks(playlist))

    def update_info(self, song_ms):
        self.last_turned_to = datetime.now()
        self.last_track_time = song_ms

    def skip_ahead(self, ms=0, db=None):
        if not ms:
            ms = TimeUtils.ms_difference(self.last_turned_to, datetime.now())

        while ms > 0:
            track = self.tracks[self.current_song_index]
            if not track.duration:
                if db:
                    duration = db.get(track.track_id)

                    if duration:
                        track.duration = duration

            if track.duration:
                song_ms = track.duration - self.last_track_time

                if ms > song_ms: 
                    self.current_song_index = (self.current_song_index + 1) % len(self.tracks)
                    self.last_track_time = 0
                else:
                    self.last_track_time += ms

                ms -= song_ms
            
            else:
                ms = 0
                self.current_song_index = (self.current_song_index + 1) % len(self.tracks)
                self.last_track_time = 0

    def current_song(self) -> Track:
        return self.tracks[self.current_song_index]

    def prev_song(self) -> Track:
        self.current_song_index = (self.current_song_index - 1) % len(self.tracks)
        self.last_track_time = 0
        return self.tracks[self.current_song_index]

    def next_song(self) -> Track:
        self.current_song_index = (self.current_song_index + 1) % len(self.tracks)
        self.last_track_time = 0
        return self.tracks[self.current_song_index]

    def get_tracks(self, playlist):
        if not playlist:
            return []

        tracks = []
        for track in playlist['tracks']:
            if track.get('track') != None:
                track_id = track['trackId'] 
                name = track['track']['title']
                duration = int(track['track']['durationMillis'])
                myTrack = Track(name, track_id, duration)
                tracks.append(myTrack)
            else:
                track_id = track['trackId']
                myTrack = Track('', track_id, 0)
                tracks.append(myTrack)

        return tracks

    def randomize_tracks(self, track_list):
        new_tracks = []
        while len(track_list):
            index = random.randrange(0, len(track_list))
            new_tracks.append(track_list.pop(index))
        return new_tracks        

    def __str__(self):
        return self.name

    def __eq__(self, other):
        if type(other) == str:
            return self.name == other
        elif type(other) == RadioStation:
            return self.name == other.name
        return False

class Radio:
    def __init__(self, station_names, filepath=None):
        #Google Auth
        client = Mobileclient()
        if filepath == None:
            filepath = client.OAUTH_FILEPATH
        if not os.path.isfile(filepath): 
            client.perform_oauth(filepath) 

        self.db = redis.Redis()
        
        client.oauth_login(client.FROM_MAC_ADDRESS, oauth_credentials=filepath)
        self.client = client

        #Set up VLC 
        self.player = vlc.MediaPlayer()
        self.stations = self.create_radio_stations(station_names)

        if self.stations:
            self.current_station = self.stations[0]

        self.timer = None

    def play(self):
        self.play_track(self.current_station.current_song(), self.current_station.last_track_time)

    def stop(self):
        if self.player.get_state() == 3: #playing
            song_time = self.player.get_time()
            self.current_station.update_info(song_time)

            self.player.stop()
            if self.timer:
                self.timer.cancel()

    def next(self, *args, **kwargs):
        track = self.current_station.next_song()
        self.play_track(track)
    
    def prev(self):
        track = self.current_station.prev_song()
        self.play_track(track)

    def switch(self, name=None):
        #log current time to the radio stations 'last tuned in' variable
        #stop playing current song
        #switch station to new station
        #change current station variable
        self.stop()

        if not name: #go to next station
            station_index = self.stations.index(self.current_station)
            self.current_station = self.stations[(station_index + 1) % len(self.stations)]

        elif name not in self.stations:
            return
        
        else:
            station_index = self.stations.index(name)
            self.current_station = self.stations[(station_index + 1) % len(self.stations)]

        self.current_station.skip_ahead()
        self.play()
            
    def play_track(self, track, ms=0):
        self.stop()

        url = self.client.get_stream_url(track.track_id, device_id=ANDROID_DEVICE_ID)
        s = str(int(TimeUtils.ms_to_sec(ms)))
        self.player.set_mrl(url, 'start-time=' + s)
        print('Playing... ' + str(track))
        self.player.play()
        length = -1
        while length == -1 or length == 0:
            length = self.player.get_length()

        if not self.db.get(track.track_id):
            self.db.set(track.track_id, length)

        song_ms = length - ms 
        song_sec = TimeUtils.ms_to_sec(song_ms)
        
        self.timer = ElapsedTimer(song_sec, self.next)
        self.timer.start()

    def create_radio_stations(self, station_names : list):
        stations = []

        if not station_names:
            return stations 

        playlists = self.client.get_all_user_playlist_contents() 
        for station_name in station_names:
            station = self.create_radio_station(station_name, playlists)

            if station:
                stations.append(station)
        
        return stations

    def create_radio_station(self, station_name, playlists):
        for playlist in playlists:
            if station_name == playlist['name']:
                station = RadioStation(station_name, playlist) 
                break
        
        return station
   
class MusicBot(irc.bot.SingleServerIRCBot):

    def __init__(self, username, client_id, oauth_token, channel, superusers, station_names=None, filepath=None):
        print(TimeUtils.datetime_to_ms(TimeUtils.epoch))
        #Twitch Auth
        self.username = username
        self.client_id = client_id
        self.oauth_token = oauth_token
        self.channel = '#' + channel

        self.channel_url = 'https://api.twitch.tv/kraken/users?login=' + channel
        headers = {'Client-ID': client_id, 'Accept': 'application/vnd.twitchtv.v5+json'}
        r = requests.get(self.channel_url, headers=headers).json()
        self.channel_id = r['users'][0]['_id']

        self.radio = Radio(station_names, filepath)
        self.superusers = superusers

        print('Connecting to ' + HOST + ' on port ' + str(PORT))
        irc.bot.SingleServerIRCBot.__init__(self, [(HOST, PORT, oauth_token)], username, username)

    def on_welcome(self, c, e):
        #Request capabilities
        c.cap('REQ', ':twitch.tv/membership')
        c.cap('REQ', ':twitch.tv/tags')
        c.cap('REQ', ':twitch.tv/commands')
        c.join(self.channel)
        print('Joined ' + self.channel)
        self.radio.play()

    adminCommands = ['play', 'stop', 'next', 'prev', 'switch']
    commands = ['song']

    def on_pubmsg(self, c, e):
        if e.arguments[0][0] != '!':
            return

        args = e.arguments[0][1:].split(' ')
        if len(args) and args[0] in MusicBot.adminCommands:
            if c.real_nickname.lower() in self.superusers:
                self.do_admin_command(args)


    def do_admin_command(self, args):
        command = args[0]
        if len(args) == 1:
            exec('self.radio.' + command + '()')

        elif len(args) == 2:
            if command == 'switch':
                station = args[1]
                if station in self.radio.stations:
                    self.radio.switch(station)


if __name__ == '__main__':
    bot = MusicBot(USERNAME, CLIENT_ID, OAUTH_TOKEN, CHANNEL, SUPERUSERS, RADIO_STATIONS)
    bot.start()