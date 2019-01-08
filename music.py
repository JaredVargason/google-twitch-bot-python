from gmusicapi import Mobileclient, Musicmanager
from datetime import datetime, timedelta
import irc.bot
import os.path
import requests
import vlc
import random
import redis
from threading import Timer, Thread
import sys

from settings import TwitchConfig, GoogleConfig, MUSIC_VOTE_INTERVAL_MINUTES

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

    @classmethod
    def minutes_to_seconds(cls, m):
        return m * 60

class ElapsedTimer(Timer):
    def __init__(self, interval, function, *args, **kwargs):
        self.start_time = None
        Timer.__init__(self, interval, function, *args, **kwargs)
    
    def start(self):
        self.start_time = datetime.now()
        Timer.start(self)

    def elapsed(self):
        if self.start_time:
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

    def __repr__(self):
        string = self.name + '\n' + self.track_id + '\n'
        return string

class RadioStation:
    def __init__(self, name, playlist=None):
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

    def __repr__(self):
        string = self.name + '\n'
        string += str(self.current_song_index)
        string += str(len(self.tracks)) + '\n'
        for track in self.tracks:
            string += repr(track)
        return string

    def __eq__(self, other):
        if type(other) == str:
            return self.name == other
        elif type(other) == RadioStation:
            return self.name == other.name
        return False

class Radio:
    def __init__(self, radio_filepath=None):
        self.android_device_id = GoogleConfig.ANDROID_DEVICE_ID
        self.google_filepath = GoogleConfig.OAUTH_FILEPATH
        self.station_names = GoogleConfig.RADIO_STATIONS

        self.db = redis.Redis()
        self.radio_filepath = radio_filepath

        #Google Auth
        client = Mobileclient()

        if self.google_filepath == None:
            self.google_filepath = client.OAUTH_FILEPATH
        if not os.path.isfile(self.google_filepath): 
            client.perform_oauth(self.google_filepath) 
        
        client.oauth_login(client.FROM_MAC_ADDRESS, oauth_credentials=self.google_filepath)
        self.client = client

        #Set up VLC 
        self.player = vlc.MediaPlayer()
        self.stations = self.create_radio_stations(self.station_names)

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

    def switch(self, index=-1):
        if not -1 <= index < len(self.stations): 
            return

        new_station = None

        if index == -1: #go to next station
            current_index = self.stations.index(self.current_station)
            new_station = self.stations[(current_index + 1) % len(self.stations)]
             
        else: #go to specific station
            new_station = self.stations[index]

        if self.current_station != new_station:
            self.stop()
            self.current_station = new_station
            self.current_station.skip_ahead()
            self.play()

    def play_track(self, track, ms=0):
        self.stop()

        url = self.client.get_stream_url(track.track_id, device_id=self.android_device_id)
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
    
    def __repr__(self):
        string = str(self.current_station) + '\n'
        string += str(len(self.station_names)) + '\n'
        for station in self.stations:
            string += repr(station)

class Poll:
    def __init__(self, options : list, minutes=15, callback=lambda: None):
        self.option_names = options
        self.votes = [0] * len(options)
        self.voters = set()
        self.duration = TimeUtils.minutes_to_seconds(minutes)
        self.open = False
        self.timer = None
        self.callback = callback

    def start(self):
        self.open = True
        self.timer = ElapsedTimer(self.duration, self.callback) 
        self.timer.start()

    def end(self):
        self.open = False
        self.timer.cancel() 

    def add_vote(self, voter, index):
        if self.open and voter not in self.voters:
            try:
                index = int(index)
                if 0 <= index < len(self.option_names):
                    self.voters.add(voter)
                    self.votes[index] += 1
            except ValueError:
                pass

    def leader(self) -> int:
        maxVotes = max(self.votes)
        maxIndex = self.votes.index(maxVotes)
        return maxIndex

    def restart(self):
        self.end()
        self.clear()
        self.start()

    def clear(self):
        self.voters.clear()
        self.votes = [0] * len(self.option_names)

    def __str__(self):
        string = ''
        for i, option in enumerate(self.option_names):
            string += option + ': ' + str(self.votes[i]) + ', '
        return string.rstrip(', ')

class MusicBot(irc.bot.SingleServerIRCBot):

    def __init__(self):
        #Twitch Auth
        self.username = TwitchConfig.USERNAME
        self.client_id = TwitchConfig.CLIENT_ID
        self.oauth_token = TwitchConfig.OAUTH_TOKEN
        self.channel = '#' + TwitchConfig.CHANNEL
        self.superusers = TwitchConfig.SUPERUSERS
        self.host = TwitchConfig.HOST
        self.port = TwitchConfig.PORT

        self.channel_url = 'https://api.twitch.tv/kraken/users?login=' + self.channel[1:]
        headers = {'Client-ID': self.client_id, 'Accept': 'application/vnd.twitchtv.v5+json'}
        r = requests.get(self.channel_url, headers=headers).json()
        self.channel_id = r['users'][0]['_id']

        print('Connecting to Google Auth...')
        self.radio = Radio()
        self.music_poll = Poll(self.radio.station_names, minutes=MUSIC_VOTE_INTERVAL_MINUTES, callback=self._music_poll_callback)
        self.last_help_command_time = datetime.now()

        print('Connecting to ' + str(self.host) + ' on port ' + str(self.port) + '...')
        irc.bot.SingleServerIRCBot.__init__(self, [(self.host, self.port, self.oauth_token)], 
                                                self.username, self.username)

    def on_welcome(self, c, e):
        #Request capabilities
        c.cap('REQ', ':twitch.tv/membership')
        c.cap('REQ', ':twitch.tv/tags')
        c.cap('REQ', ':twitch.tv/commands')
        c.join(self.channel)
        print('Joined ' + self.channel)
        self.radio.play()
        self.music_poll.start()

    commands = ['vote']
    admin_commands = ['play', 'stop', 'next', 'prev', 'switch', 'votes', 'quit']
    help_commands = ['vote_time', 'song', 'stations']

    def on_pubmsg(self, c, e):
        if not e.arguments[0].startswith('!'):
            return
        
        args = e.arguments[0][1:].split(' ')
        command = args[0]

        if command in MusicBot.commands:
            self.do_command(c, command, args)

        elif command in MusicBot.help_commands:
            self.do_help_command(c, command, args)

        elif command in MusicBot.admin_commands:
            if c.real_nickname.lower() in self.superusers:
                self.do_admin_command(command, args)

    def on_privmsg(self, c, e):
        if c.real_nickname.lower() in self.superusers:
            self.on_pubmsg(c, e)

    def do_command(self, c, cmd, args):
        if len(args) == 2:
            if cmd == 'vote':
                self.music_poll.add_vote(c.real_nickname, args[1])

    def do_help_command(self, c, cmd, args):
        if len(args) == 1:
            now = datetime.now()
            if (now - self.last_help_command_time).total_seconds() > 10:
                self.last_help_command_time = now
                if cmd == 'vote_time':
                    self.vote_time()
                elif cmd == 'song':
                    self.song()
                elif cmd == 'stations':
                    self.stations()

    def do_admin_command(self, cmd, args):
        if len(args) == 1:
            if cmd == 'play':
                self.radio.play()
            elif cmd == 'stop':
                self.radio.stop()
            elif cmd == 'next':
                self.radio.next()
            elif cmd == 'prev':
                self.radio.prev()
            elif cmd == 'switch':
                self.radio.switch()
            elif cmd == 'votes':
                self.votes()
            elif cmd == 'quit':
                self.quit()

        elif len(args) == 2:
            if cmd == 'switch':
                index = args[1]
                if str.isdigit(index):
                    self.radio.switch(int(index))

    def _music_poll_callback(self): 
        self.radio.switch(self.music_poll.leader())
        self.music_poll.restart()

    def vote_time(self):
        c = self.connection
        time_left = self.music_poll.duration - TimeUtils.ms_to_sec(self.music_poll.timer.elapsed())
        c.privmsg(self.channel, str(time_left) + ' ms left')

    def stations(self):
        c = self.connection
        c.privmsg(self.channel, ', '.join([str(i) + ': ' + str(station) for i, station in enumerate(self.radio.stations)]))

    def song(self):
        c = self.connection
        song_name = str(self.radio.current_station.current_song())
        c.privmsg(self.channel, song_name)

    def votes(self):
        c = self.connection
        c.privmsg(self.channel, str(self.music_poll))
    
    def quit(self):
        self.radio.stop()
        self.music_poll.end()
        sys.exit(0)

if __name__ == '__main__':
    bot = MusicBot()
    bot.start()
