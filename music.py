from gmusicapi import Mobileclient, Musicmanager
import irc.bot
import os.path
import requests
import vlc
import time

from settings import (HOST, PORT, USERNAME, CLIENT_ID, 
    OAUTH_TOKEN, CHANNEL, ANDROID_DEVICE_ID)

RADIO_STATIONS = ['Dad Rock Radio', '105.5, The Giraffe', 'AnimeCentral', 'NintendoRadio']

#JARED TO DO FOR 1/2/2018
#FINISH SYNCED RADIO STATIONS- SAVE LAST SONGS/TIMESTAMP IN DICT?
#TIDY UP THIS SCRIPT AND REQUIREMENTS FILES
#CREATE REQUIREMENTS-DEV.TXT FILE TO USE ACROSS PROJECTS
#FIX RC CAR UP
#WORK OUT
#GO OVER RESUME AGAIN
#APPLY TO JOB
#SHOWER

class MusicBot(irc.bot.SingleServerIRCBot):
    def __init__(self, username, client_id, oauth_token, channel, filepath=None):
        #Twitch Auth
        self.username = username
        self.client_id = client_id
        self.oauth_token = oauth_token
        self.channel = '#' + channel

        self.channel_url = 'https://api.twitch.tv/kraken/users?login=' + channel
        headers = {'Client-ID': client_id, 'Accept': 'application/vnd.twitchtv.v5+json'}
        r = requests.get(self.channel_url, headers=headers).json()
        self.channel_id = r['users'][0]['_id']

        #Google Auth
        client = Mobileclient()
        if filepath == None:
            filepath = client.OAUTH_FILEPATH
        if not os.path.isfile(filepath): 
            client.perform_oauth(filepath) 
        
        client.oauth_login(client.FROM_MAC_ADDRESS, oauth_credentials=filepath)

        self.client = client
        ids = self.get_playlist_track_ids('Dad Rock Radio')
        self.play_track(ids[0])
        self.vlc = vlc.Instance()
        self.player = vlc.MediaPlayer()

        self.start()

    def on_welcome(self, c, e):
        print('Joining ' + self.channel)

        #Request capabilities
        c.cap('REQ', ':twitch.tv/membership')
        c.cap('REQ', ':twitch.tv/tags')
        c.cap('REQ', ':twitch.tv/commands')
        c.join(self.channel)
        print('Joined ' + self.channel)

    def play_track(self, track_id, ms=0):
        url = self.client.get_stream_url(track_id, device_id=ANDROID_DEVICE_ID)
        self.player.set_mrl(url)
        #self.player.set_time()
        if ms != 0:
            self.player.set_time(ms)
        
    def get_playlist_track_ids(self, name: str):
        playlist = self.get_playlist(name)
        if not playlist:
            return None

        tracks = playlist['tracks']
        track_ids = [track['trackId'] for track in tracks]
        return track_ids

    def get_playlist(self, name: str):
        playlists = self.client.get_all_user_playlist_contents()
        for playlist in playlists: 
            if name == playlist['name']:
                return playlist
        
        return None

if __name__ == '__main__':
    bot = MusicBot()