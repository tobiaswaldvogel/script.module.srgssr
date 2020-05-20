# -*- coding: utf-8 -*-

# Copyright (C) 2018 Alexander Seiler
#
#
# This file is part of script.module.srgssr.
#
# script.module.srgssr is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# script.module.srgssr is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with script.module.srgssr.
# If not, see <http://www.gnu.org/licenses/>.

import os
import sys
import re
import traceback

import datetime
import json
import requests

try:  # Python 3
    from urllib.parse import quote_plus, parse_qsl, ParseResult
    from urllib.parse import urlparse as urlps
except ImportError:  # Python 2
    from urllib import quote_plus
    from urlparse import parse_qsl, ParseResult
    from urlparse import urlparse as urlps

from kodi_six import xbmc, xbmcgui, xbmcplugin, xbmcaddon, xbmcvfs
from simplecache import SimpleCache
import utils
import youtube_channels


ADDON_ID = 'script.module.srgssr'
REAL_SETTINGS = xbmcaddon.Addon(id=ADDON_ID)
ADDON_NAME = REAL_SETTINGS.getAddonInfo('name')
ADDON_VERSION = REAL_SETTINGS.getAddonInfo('version')
ICON = REAL_SETTINGS.getAddonInfo('icon')
LANGUAGE = REAL_SETTINGS.getLocalizedString
TIMEOUT = 30

IDREGEX = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+'

FAVOURITE_SHOWS_FILENAME = 'favourite_shows.json'
YOUTUBE_CHANNELS_FILENAME = 'youtube_channels.json'
RECENT_SHOW_SEARCHES_FILENAME = 'recently_searched_shows.json'
RECENT_MEDIA_SEARCHES_FILENAME = 'recently_searched_medias.json'

try:
    KODI_VERSION = int(xbmc.getInfoLabel("System.BuildVersion").split('.')[0])
except:
    KODI_VERSION = 16

def get_params():
    """
    Parses the Kodi plugin URL and returns its parameters
    in a dictionary.
    """
    return dict(parse_qsl(sys.argv[2][1:]))


class SRGSSR(object):
    """
    Base class for all SRG SSR related plugins.
    Everything that can be done independently from the business unit
    (SRF, RTS, RSI, etc.) should be done here.
    """
    def __init__(self, plugin_handle, bu='srf', addon_id=ADDON_ID):
        self.handle = plugin_handle
        self.cache = SimpleCache()
        self.real_settings = xbmcaddon.Addon(id=addon_id)
        self.bu = bu
        self.addon_id = addon_id
        self.icon = self.real_settings.getAddonInfo('icon')
        self.fanart = self.real_settings.getAddonInfo('fanart')
        self.language = LANGUAGE
        self.plugin_language = self.real_settings.getLocalizedString
        self.host_url = 'https://www.%s.ch' % bu
        self.apiv3_url = None
        if bu == 'swi':
            self.host_url = 'https://play.swissinfo.ch'
        if bu == 'srf':
            self.apiv3_url = self.host_url + '/play/v3/api/srf/production/'
        self.data_uri = ('special://home/addons/%s/resources/'
                         'data') % self.addon_id
        self.media_uri = ('special://home/addons/%s/resources/'
                          'media') % self.addon_id

        # Plugin options:
        self.debug = self.get_boolean_setting(
            'Enable_Debugging')
        self.segments = self.get_boolean_setting(
            'Enable_Show_Segments')
        self.segments_topics = self.get_boolean_setting(
            'Enable_Segments_Topics')
        self.subtitles = self.get_boolean_setting(
            'Extract_Subtitles')
        self.prefer_hd = self.get_boolean_setting(
            'Prefer_HD')
        self.number_of_episodes = 10

        # Delete temporary subtitle files urn*.vtt
        clean_dir = 'special://temp'
        dirname, filenames = xbmcvfs.listdir(clean_dir)
        for filename in filenames:
            if filename.startswith('urn') and filename.endswith('.vtt'):
                xbmcvfs.delete(clean_dir + '/' + filename)

    def get_youtube_icon(self):
        path = os.path.join(
            xbmc.translatePath(self.media_uri), 'icon_youtube.png')
        if os.path.exists(path):
            return path
        return self.icon

    def get_boolean_setting(self, setting):
        """
        Returns the boolean value of a specified setting.

        Keyword arguments
        setting  -- the setting option to check
        """
        return self.real_settings.getSetting(setting) == 'true'

    def log(self, msg, level=xbmc.LOGDEBUG):
        """
        Logs a message using Kodi's logging interface.

        Keyword arguments:
        msg   -- the message to log
        level -- the logging level
        """
        if self.debug:
            if level == xbmc.LOGERROR:
                msg += ' ,' + traceback.format_exc()
        message = ADDON_ID + '-' + ADDON_VERSION + '-' + msg
        xbmc.log(msg=message, level=level)

    @staticmethod
    def build_url(mode=None, name=None, url=None, page_hash=None, page=None):
        """Build a URL for the Kodi plugin.

        Keyword arguments:
        mode      -- an integer representing the mode
        name      -- a string containing some information, e.g. a video id
        url       -- a plugin URL, if another plugin/script needs to called
        page_hash -- a string (used to get additional videos through the API)
        page      -- an integer used to indicate the current page in
                     the list of items
        """
        if mode:
            mode = str(mode)
        if page:
            page = str(page)
        added = False
        queries = (url, mode, name, page_hash, page)
        query_names = ('url', 'mode', 'name', 'page_hash', 'page')
        purl = sys.argv[0]
        for query, qname in zip(queries, query_names):
            if query:
                add = '?' if not added else '&'
                purl += '%s%s=%s' % (add, qname, quote_plus(query))
                added = True
        return purl

    def open_url(self, url, use_cache=True):
        """Open and read the content given by a URL.

        Keyword arguments:
        url       -- the URL to open as a string
        use_cache -- boolean to indicate if the cache provided by the
                     Kodi module SimpleCache should be used (default: True)
        """
        self.log('open_url, url = ' + str(url))
        cache_response = None
        if use_cache:
            cache_response = self.cache.get(
                ADDON_NAME + '.open_url, url = %s' % url)
        if not cache_response:
            headers = {
                'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64; rv:59.0)'
                               'Gecko/20100101 Firefox/59.0'),
            }
            response = requests.get(url, headers=headers)
            if not response.ok:
                self.log('open_url: Failed to open url %s' % url)
                xbmcgui.Dialog().notification(
                    ADDON_NAME, LANGUAGE(30100), ICON, 4000)
                return ''
            self.cache.set(
                ADDON_NAME + '.open_url, url = %s' % url,
                response.text,
                expiration=datetime.timedelta(hours=2))
            return response.text
        return self.cache.get(ADDON_NAME + '.open_url, url = %s' % url)

    def build_main_menu(self, identifiers=[]):
        """
        Builds the main menu of the plugin:

        Keyword arguments:
        identifiers  -- A list of strings containing the identifiers
                        of the menus to display.
        """
        self.log('build_main_menu')
        main_menu_list = [
            {
                # All shows
                'identifier': 'All_Shows',
                'name': self.plugin_language(30050),
                'mode': 10,
                'displayItem': self.get_boolean_setting('All_Shows'),
                'icon': self.icon,
            }, {
                # Favourite shows
                'identifier': 'Favourite_Shows',
                'name': self.plugin_language(30051),
                'mode': 11,
                'displayItem': self.get_boolean_setting('Favourite_Shows'),
                'icon': self.icon,
            }, {
                # Newest favourite shows
                'identifier': 'Newest_Favourite_Shows',
                'name': self.plugin_language(30052),
                'mode': 12,
                'displayItem': self.get_boolean_setting(
                    'Newest_Favourite_Shows'),
                'icon': self.icon,
            }, {
                # Recommendations
                'identifier': 'Recommendations',
                'name': self.plugin_language(30053),
                'mode': 16,
                'displayItem': self.get_boolean_setting('Recommendations'),
                'icon': self.icon,
            }, {
                # Newest shows
                'identifier': 'Newest_Shows',
                'name': self.plugin_language(30054),
                'mode': 13,
                'displayItem': self.get_boolean_setting('Newest_Shows'),
                'icon': self.icon,
            }, {
                # Most clicked shows
                'identifier': 'Most_Clicked_Shows',
                'name': self.plugin_language(30055),
                'mode': 14,
                'displayItem': self.get_boolean_setting('Most_Clicked_Shows'),
                'icon': self.icon,
            }, {
                # Soon offline
                'identifier': 'Soon_Offline',
                'name': self.plugin_language(30056),
                'mode': 15,
                'displayItem': self.get_boolean_setting('Soon_Offline'),
                'icon': self.icon,
            }, {
                # Shows by date
                'identifier': 'Shows_By_Date',
                'name': self.plugin_language(30057),
                'mode': 17,
                'displayItem': self.get_boolean_setting('Shows_By_Date'),
                'icon': self.icon,
            }, {
                # Live TV
                'identifier': 'Live_TV',
                'name': self.plugin_language(30072),
                'mode': 26,
                'displayItem': self.get_boolean_setting('Live_TV'),
                'icon': self.icon,
            }, {
                # SRF.ch live
                'identifier': 'SRF_Live',
                'name': self.plugin_language(30070),
                'mode': 18,
                'displayItem': self.get_boolean_setting('SRF_Live'),
                'icon': self.icon,
            }, {
                # Search
                'identifier': 'Search',
                'name': self.plugin_language(30085),
                'mode': 27,
                'displayItem': self.get_boolean_setting('Search'),
                'icon': self.icon,
            }, {
                # YouTube
                'identifier': '%s_YouTube' % self.bu.upper(),
                'name': self.plugin_language(30074),
                'mode': 30,
                'displayItem': self.get_boolean_setting(
                    '%s_YouTube' % self.bu.upper()),
                'icon': self.get_youtube_icon(),
            }, {
                # Channels
                'identifier': 'Radio_Channels',
                'name': self.plugin_language(30075),
                'mode': 40,
                'displayItem': self.get_boolean_setting('Radio_Channels'),
                'icon': self.icon,
            }, {
                # Newest audios
                'identifier': 'Newest_Audios',
                'name': self.plugin_language(30076),
                'mode': 45,
                'displayItem': False,
                'icon': self.icon,
            }, {
                # Most listened
                'identifier': 'Most_Listened',
                'name': self.plugin_language(30077),
                'mode': 46,
                'displayItem': self.get_boolean_setting('Most_Listened'),
                'icon': self.icon,
            }, {
                # Live radio
                'identifier': 'Live_Radio',
                'name': self.plugin_language(30078),
                'mode': 47,
                'displayItem': self.get_boolean_setting('Live_Radio'),
                'icon': self.icon,
            }, {
                # Shows (by topic)
                'identifier': 'Shows_Topics',
                'name': self.plugin_language(30079),
                'mode': 48,
                'displayItem': self.get_boolean_setting('Shows_Topics'),
                'icon': self.icon,
            }
        ]
        folders = []
        for ide in identifiers:
            item = next((e for e in main_menu_list if
                         e['identifier'] == ide), None)
            if item:
                folders.append(item)
        self.build_folder_menu(folders)

    def build_folder_menu(self, folders):
        """
        Builds a menu from a list of folder dictionaries. Each dictionary
        must have the key 'name' and can have the keys 'identifier', 'mode',
        'displayItem', 'icon', 'purl' (a dictionary to build the plugin url).
        """
        for item in folders:
            if item.get('displayItem') is not False:
                list_item = xbmcgui.ListItem(label=item['name'])
                list_item.setProperty('IsPlayable', 'false')
                list_item.setArt({'thumb' : item['icon'], 'fanart': self.fanart})
                purl_dict = item.get('purl', {})
                mode = purl_dict.get('mode') or item.get('mode')
                uname = purl_dict.get('name') or item.get('identifier')
                purl = self.build_url(
                    mode=mode, name=uname)
                xbmcplugin.addDirectoryItem(
                    handle=self.handle, url=purl,
                    listitem=list_item, isFolder=True)

    def build_menu_apiv3(self, queries, mode, page=None, page_hash=None, name='',
                         include_segments=False, segment_option=False):
        """
        Builds a menu based on the API v3, which is supposed to be more stable
        
        Keyword arguments:
        queries      -- an individual API to call with cursor support 
                        or a list of apis to concatenate
        mode         -- mode for the URL of the next folder
        page         -- for compatibility, same as page_hash
        page_hash    -- cursor for fetching the next items
        name         -- name of the list
        """
        # prefer build_entry over build_episode_menu
        # to save an extra lookup
        if isinstance(queries, list):
            # Build a combined and sorted list for several queries
            items = []
            for query in queries:
                data = json.loads(self.open_url(self.apiv3_url + query))
                if data and 'data' in data:
                    data = data['data']
                    if 'data'    in data: data = data['data']
                    if 'results' in data: data = data['results']
                    for item in data:
                        items.append(item)

            items.sort(key=lambda item: item['date'], reverse=True)
            for item in items:
                if include_segments or segment_option:
                    self.build_episode_menu(item['id'],
                                    include_segments=include_segments,
                                    segment_option=segment_option)
                else:
                    self.build_entry(item)
            return

        if page:        cursor = page
        elif page_hash: cursor = page_hash
        else:           cursor = None

        if cursor:
            queries += ('&' if '?' in queries else '?') + 'next=' + cursor

        data = json.loads(self.open_url(self.apiv3_url + queries))
        try: data = data['data']
        except:
            self.log('No media found.')
            return

        if   'data'    in data: items = data['data']
        elif 'results' in data: items = data['results']
        else:                   items = data

        for item in items:
            if include_segments or segment_option:
                self.build_episode_menu(item['id'],
                                    include_segments=include_segments,
                                    segment_option=segment_option)
            else:
                self.build_entry(item)

        if 'next' in data:
            cursor = data['next']
            self.log('next: ' + cursor)

            if page is not None:
                url = self.build_url(mode=mode, name=name, page=cursor)
            elif page_hash is not None:
                url = self.build_url(mode=mode, name=name, page_hash=cursor)
            else:
                return

            next_item = xbmcgui.ListItem(label='>> ' + LANGUAGE(30073))  # Next page 
            next_item.setProperty('IsPlayable', 'false')                               
            xbmcplugin.addDirectoryItem(self.handle, url, next_item, isFolder=True)                                    

    # TODO: Check, if this can be replaced by extract_shows_information,
    # like it is already done for radio shows.
    def read_all_available_shows(self):
        """
        Downloads a list of all available shows and returns this list.

        This works for the business units 'srf', 'rts', 'rsi' and 'rtr', but
        not for 'swi'.
        """
        if self.apiv3_url:
            data = json.loads(self.open_url(self.apiv3_url + 'shows'))
            try:    return data['data']
            except: return []

        json_url = ('http://il.srgssr.ch/integrationlayer/1.0/ue/%s/tv/'
                    'assetGroup/editorialPlayerAlphabetical.json') % self.bu
        json_response = json.loads(self.open_url(json_url))
        show_list = utils.try_get(
            json_response,
            ('AssetGroups', 'Show'), data_type=list, default=[])
        if not show_list:
            self.log('read_all_available_shows: No shows found.')
            return []
        return show_list

    def build_all_shows_menu(self, favids=None):
        """
        Builds a list of folders containing the names of all the current
        shows.

        Keyword arguments:
        favids -- A list of show ids (strings) respresenting the favourite
                  shows. If such a list is provided, only the folders for
                  the shows on that list will be build. (default: None)
        """
        self.log('build_all_shows_menu')
        show_list = self.read_all_available_shows()

        list_items = []
        for jse in show_list:
            title = utils.try_get(jse, 'title')
            show_id = utils.try_get(jse, 'id')
            if not (title and show_id):
                self.log(
                    'build_all_shows_menu: Skipping, no title or id found.')
                continue

            # Skip if we build the 'favourite show menu' and the current
            # show id is not in our favourites:
            if favids is not None and show_id not in favids:
                continue

            list_item = xbmcgui.ListItem(label=title)
            list_item.setProperty('IsPlayable', 'false')
            list_item.setInfo(
                'video',
                {
                    'title': title,
                    'plot': utils.try_get(
                        jse, 'lead') or utils.try_get(jse, 'description'),
                }
            )

            try: image_url = jse['imageUrl']
            except:
                image_url = utils.try_get(
                    jse,
                    ('Image', 'ImageRepresentations',
                    'ImageRepresentation', 0, 'url'))
            if image_url:
                image_url = re.sub(r'/\d+x\d+', '', image_url)
                thumbnail = image_url + '/scale/width/688'
                banner = image_url.replace(
                    'WEBVISUAL',
                    'HEADER_SRF_PLAYER')
            else:
                image_url = self.fanart
                thumbnail = self.icon
                banner = None

            list_item.setArt({
                'thumb': thumbnail,
                'poster': image_url,
                'fanart': image_url,
                'banner': banner,
            })
            url = self.build_url(mode=20, name=show_id)
            list_items.append((url, list_item, True))
        xbmcplugin.addDirectoryItems(
            self.handle, list_items, totalItems=len(list_items))

    def build_favourite_shows_menu(self):
        """
        Builds a list of folders for the favourite shows.
        """
        self.log('build_favourite_shows_menu')
        favourite_show_ids = self.read_favourite_show_ids()
        self.build_all_shows_menu(favids=favourite_show_ids)

    def build_show_folder(self, show_id, radio_tv):
        """
        Creates a folder for a specified show.

        Keyword arguments:
        show_id   -- the id of the show
        radio_tv  -- either 'radio' or 'tv'
        """
        if self.apiv3_url:
            query_url = self.apiv3_url + 'show-detail/' + show_id
            result = json.loads(self.open_url(query_url, use_cache=True))
            if result and 'data' in result:
                show_info = result['data']
        else:
            if radio_tv not in ('radio', 'tv'):
                self.log(('build_show_folder: radio_tv must be '
                          'either \'radio\' or \'tv\''))
                return
            query_url = '%s/play/%s/show/%s/latestEpisodes' % (
                self.host_url, radio_tv, show_id)
            result = json.loads(self.open_url(query_url, use_cache=True))
            show_info = utils.try_get(result, 'show', data_type=dict, default={})

        if not show_info:
            self.log('build_show_folder: Unable to retrieve show info')
            return
        title = utils.try_get(show_info, 'title')
        if not title:
            self.log('build_show_folder: Unable to retrieve title')
            return
        list_item = xbmcgui.ListItem(label=title)
        list_item.setProperty('IsPlayable', 'false')
        list_item.setInfo('video', {
                'title': title,
                'plot': utils.try_get(
                    show_info, 'lead') or utils.try_get(
                        show_info, 'description')
            })
        image = thumbnail = utils.try_get(show_info, 'imageUrl')
        image = re.sub(r'/\d+x\d+', '', image)
        if not image:
            image = self.fanart
            thumbnail = self.icon
        banner_image = utils.try_get(show_info, 'bannerImageUrl', default=None)
        list_item.setArt({
            'thumb': thumbnail,
            'poster': image,
            'fanart': image,
            'banner': banner_image
        })
        url = self.build_url(mode=20, name=show_id)
        xbmcplugin.addDirectoryItem(self.handle, url, list_item, isFolder=True)

    def build_newest_favourite_menu(self, page=1, audio=False):
        """
        Builds a Kodi list of the newest favourite shows.

        Keyword arguments:
        page -- an integer indicating the current page on the
                list (default: 1)
        """
        self.log('build_newest_favourite_menu')
        number_of_days = 30
        show_ids = self.read_favourite_show_ids()

        if self.apiv3_url:
            queries = []
            for sid in show_ids:
                queries.append('videos-by-show-id?showId=' + sid)
            return self.build_menu_apiv3(queries, 12)

        # TODO: This depends on the local time settings
        now = datetime.datetime.now()
        current_month_date = datetime.date.today().strftime('%m-%Y')
        list_of_episodes_dict = []
        banners = {}
        section = 'radio' if audio else 'tv'
        for sid in show_ids:
            json_url = ('%s/play/%s/show/%s/latestEpisodes?numberOfEpisodes=%d'
                        '&tillMonth=%s') % (self.host_url, section, sid,
                                            number_of_days, current_month_date)
            self.log('build_newest_favourite_menu. Open URL %s.' % json_url)
            response = json.loads(self.open_url(json_url))
            banner_image = utils.try_get(
                response,
                ('show', 'bannerImageUrl'))
            if re.match(r'.+/\d+x\d+$', banner_image):
                banner_image += '/scale/width/1000'

            episode_list = utils.try_get(
                response, 'episodes', data_type=list, default=[])
            for episode in episode_list:
                date_time = utils.parse_datetime(
                    utils.try_get(episode, 'date'))
                if date_time and \
                        date_time >= now + datetime.timedelta(-number_of_days):
                    list_of_episodes_dict.append(episode)
                    banners.update(
                        {utils.try_get(episode, 'id'): banner_image})
        sorted_list_of_episodes_dict = sorted(
            list_of_episodes_dict, key=lambda k: utils.parse_datetime(
                utils.try_get(k, 'date')), reverse=True)
        try:
            page = int(page)
        except TypeError:
            page = 1
        reduced_list = sorted_list_of_episodes_dict[
            (page - 1)*self.number_of_episodes:page*self.number_of_episodes]
        for episode in reduced_list:
            segments = utils.try_get(
                episode, 'segments', data_type=list, default=[])
            is_folder = True if segments and self.segments else False
            self.build_entry(
                episode, banner=utils.try_get(episode, 'id'),
                is_folder=is_folder, audio=audio)

        if len(sorted_list_of_episodes_dict) > page * self.number_of_episodes:
            next_item = xbmcgui.ListItem(
                label='>> ' + LANGUAGE(30073))  # Next page
            next_item.setProperty('IsPlayable', 'false')
            purl = self.build_url(mode=12, page=page+1)
            xbmcplugin.addDirectoryItem(
                self.handle, purl, next_item, isFolder=True)

    def build_show_menu(self, show_id, page_hash=None, audio=False):
        """
        Builds a list of videos (can be folders in case of segmented videos)
        for a show given by its show id.

        Keyword arguments:
        show_id   -- the id of the show
        page_hash -- the page hash to get the list of
                     another page (default: None)
        audio     -- boolean value to indicate if the show is a
                     radio show (default: False)
        """
        self.log(('build_show_menu, show_id = %s, page_hash=%s, '
                  'audio=%s') % (show_id, page_hash, audio))

        if self.apiv3_url:
            cursor = page_hash if page_hash else ''
            return self.build_menu_apiv3('videos-by-show-id?showId=' + show_id,
                                         20, page_hash=cursor, name=show_id)

        # TODO: This depends on the local time settings
        current_month_date = datetime.date.today().strftime('%m-%Y')
        section = 'radio' if audio else 'tv'
        if not page_hash:
            json_url = ('%s/play/%s/show/%s/latestEpisodes?numberOfEpisodes=%d'
                        '&tillMonth=%s') % (self.host_url, section, show_id,
                                            self.number_of_episodes,
                                            current_month_date)
        else:
            json_url = ('%s/play/%s/show/%s/latestEpisodes?nextPageHash=%s'
                        '&tillMonth=%s') % (self.host_url, section, show_id,
                                            page_hash, current_month_date)

        json_response = json.loads(self.open_url(json_url))
        try:
            banner_image = utils.try_get(
                json_response, ('show', 'bannerImageUrl'))

            # Banner image urls sometimes end with '/3x1'. They are
            # only accesible if we append '/scale/width/\d+':
            if re.match(r'.+/\d+x\d+$', banner_image):
                banner_image += '/scale/width/1000'
        except KeyError:
            banner_image = None

        next_page_hash = None
        if 'nextPageUrl' in json_response:
            next_page_url = utils.try_get(json_response, 'nextPageUrl')
            next_page_hash_regex = r'nextPageHash=(?P<hash>[0-9a-f]+)'
            match = re.search(next_page_hash_regex, next_page_url)
            if match:
                next_page_hash = match.group('hash')

        json_episode_list = utils.try_get(
            json_response, 'episodes', data_type=list, default=[])
        if not json_episode_list:
            self.log('No episodes for show %s found.' % show_id)
            return

        for episode_entry in json_episode_list:
            segments = utils.try_get(
                episode_entry, 'segments', data_type=list, default=[])
            enable_segments = True if self.segments and segments else False
            self.build_entry(
                episode_entry, banner=banner_image, is_folder=enable_segments,
                audio=audio)

        if next_page_hash and page_hash != next_page_hash:
            self.log('page_hash: %s' % page_hash)
            self.log('next_hash: %s' % next_page_hash)
            next_item = xbmcgui.ListItem(
                label='>> ' + LANGUAGE(30073))  # Next page
            next_item.setProperty('IsPlayable', 'false')
            url = self.build_url(
                mode=20, name=show_id, page_hash=next_page_hash)
            xbmcplugin.addDirectoryItem(
                self.handle, url, next_item, isFolder=True)

    def build_topics_overview_menu(self, newest_or_most_clicked):
        """
        Builds a list of folders, where each folders represents a
        topic (e.g. News).

        Keyword arguments:
        newest_or_most_clicked -- a string (either 'Newest' or 'Most clicked')
        """
        self.log('build_topics_overview_menu, newest_or_most_clicked = %s' %
                 newest_or_most_clicked)
        if newest_or_most_clicked == 'Newest':
            mode = 22
        elif newest_or_most_clicked == 'Most clicked':
            mode = 23
        else:
            self.log('build_topics_overview_menu: Unknown mode, \
                must be "Newest" or "Most clicked".')
            return

        if self.apiv3_url:
            topics_json = json.loads(self.open_url(self.apiv3_url + 'topics'))
            try:    topics_json = topics_json['data']
            except: pass
        else:
            topics_url = self.host_url + '/play/tv/topicList'
            topics_json = json.loads(self.open_url(topics_url))

        if not isinstance(topics_json, list) or not topics_json:
            self.log('No topics found.')
            return
        for elem in topics_json:
            try:
                image = re.sub(r'/\d+x\d+', '', elem['imageUrl'])
                thumbnail = image + '/scale/width/688'
                banner = image.replace('WEBVISUAL', 'HEADER_SRF_PLAYER')
            except:
                image = self.fanart
                thumbnail = self.icon
                banner = image

            list_item = xbmcgui.ListItem(label=elem.get('title'))
            list_item.setProperty('IsPlayable', 'false')
            list_item.setArt({
                'thumb':  thumbnail,
                'poster': image,
                'banner': banner,
                'fanart': image
            })
            name = utils.try_get(elem, 'id')
            if name:
                purl = self.build_url(mode=mode, name=name)
                xbmcplugin.addDirectoryItem(
                    handle=self.handle, url=purl,
                    listitem=list_item, isFolder=True)

    def extract_id_list(self, url, editor_picks=False):
        """
        Opens a webpage and extracts video ids (of the form "id": "<vid>")
        from JavaScript snippets.

        Keyword argmuents:
        url           -- the URL of the webpage
        editor_picks  -- if set, only extracts ids of editor picks
                         (default: False)
        """
        self.log('extract_id_list, url = %s' % url)
        response = self.open_url(url)
        string_response = utils.str_or_none(response, default='')
        if not string_response:
            self.log('No video ids found on %s' % url)
            return []
        readable_string_response = string_response.replace('&quot;', '"')
        id_regex = r'''(?x)
                        \"id\"
                        \s*:\s*
                        \"
                        (?P<id>
                            %s
                        )
                        \"
                    ''' % IDREGEX
        if editor_picks:
            id_regex += r'.+\"isEditorPick\"\s*:\s*true'
        id_list = [m.group('id') for m in re.finditer(
            id_regex, readable_string_response)]
        return id_list

    def build_topics_menu(self, name, topic_id=None, page=1):
        """
        Builds a list of videos (can also be folders) for a given topic.

        Keyword arguments:
        name     -- the type of the list, can be 'Newest', 'Most clicked',
                    'Soon offline' or 'Trending'.
        topic_id -- the SRF topic id for the given topic, this is only needed
                    for the types 'Newest' and 'Most clicked' (default: None)
        page     -- an integer representing the current page in the list
        """
        self.log('build_topics_menu, name = %s, topic_id = %s, page = %s' %
                 (name, topic_id, page))
        number_of_videos = 50
        # editor_picks = []
        if name == 'Newest':
            url = '%s/play/tv/topic/%s/latest?numberOfVideos=%s' % (
                self.host_url, topic_id, number_of_videos)
            query = 'latest-media-by-topic?topicId=' + topic_id
            mode = 22
        elif name == 'Most clicked':
            url = '%s/play/tv/topic/%s/mostClicked?numberOfVideos=%s' % (
                self.host_url, topic_id, number_of_videos)
            query = ('trending-media-by-topics?topicIds=' + topic_id
                     + '&types=CLIP%2CSEGMENT&pageSize=50')
            mode = 23
        elif name == 'Soon offline':
            url = '%s/play/tv/videos/soon-offline-videos?numberOfVideos=%s' % (
                self.host_url, number_of_videos)
            query = 'expiring-soon'
            mode = 15
        elif name == 'Trending':
            url = ('%s/play/tv/videos/trending?numberOfVideos=%s'
                   '&onlyEpisodes=true&includeEditorialPicks=true') % (
                       self.host_url, number_of_videos)
            query = ['trending-videos','editorial-picks']
            mode = 16
            # editor_picks = self.extract_id_list(url, editor_picks=True)
            # self.log('build_topics_menu: editor_picks = %s' % editor_picks)
        else:
            self.log('build_topics_menu: Unknown mode.')
            return

        if self.apiv3_url:
            cursor = page if page else ''
            name = topic_id if topic_id else ''
            return self.build_menu_apiv3(query, mode, page=cursor, name=name,
                                         segment_option=self.segments_topics)

        id_list = self.extract_id_list(url)
        try:
            page = int(page)
        except TypeError:
            page = 1

        reduced_id_list = id_list[(page - 1) * self.number_of_episodes:
                                  page * self.number_of_episodes]
        for vid in reduced_id_list:
            self.build_episode_menu(
                vid, include_segments=False,
                segment_option=self.segments_topics)

        try:
            vid = id_list[page*self.number_of_episodes]
            next_item = xbmcgui.ListItem(
                label='>> ' + LANGUAGE(30073))  # Next page
            next_item.setProperty('IsPlayable', 'false')
            name = topic_id if topic_id else ''
            purl = self.build_url(mode=mode, name=name, page=page+1)
            xbmcplugin.addDirectoryItem(
                handle=self.handle, url=purl,
                listitem=next_item, isFolder=True)
        except IndexError:
            return

    def build_episode_menu(self, video_id, include_segments=True,
                           segment_option=False, audio=False):
        """
        Builds a list entry for a episode by a given video id.
        The segment entries for that episode can be included too.
        The video id can be an id of a segment. In this case an
        entry for the segment will be created.

        Keyword arguments:
        video_id         -- the id of the video
        include_segments -- indicates if the segments (if available) of the
                            video should be included in the list
                            (default: True)
        segment_option   -- Which segment option to use.
                            (default: False)
        audio            -- boolean value to indicate if the episode is a
                            radio show (default: False)
        """
        self.log('build_episode_menu, video_id = %s, include_segments = %s' %
                 (video_id, include_segments))
        content_type = 'audio' if audio else 'video'
        json_url = ('https://il.srgssr.ch/integrationlayer/2.0/%s/'
                    'mediaComposition/%s/%s.json') % (self.bu, content_type,
                                                      video_id)
        self.log('build_episode_menu. Open URL %s' % json_url)
        try:
            json_response = json.loads(self.open_url(json_url))
        except Exception:
            self.log('build_episode_menu: Cannot open media json for %s.'
                     % video_id)
            return

        chapter_urn = utils.try_get(json_response, 'chapterUrn')
        segment_urn = utils.try_get(json_response, 'segmentUrn')

        id_regex = r'[a-z]+:[a-z]+:[a-z]+:(?P<id>.+)'
        match_chapter_id = re.match(id_regex, chapter_urn)
        match_segment_id = re.match(id_regex, segment_urn)
        chapter_id = match_chapter_id.group('id') if match_chapter_id else None
        segment_id = match_segment_id.group('id') if match_segment_id else None

        if not chapter_id:
            self.log('build_episode_menu: No valid chapter URN \
                available for video_id %s' % video_id)
            return

        try:
            banner = utils.try_get(json_response, ('show', 'bannerImageUrl'))
            if re.match(r'.+/\d+x\d+$', banner):
                banner += '/scale/width/1000'
        except KeyError:
            banner = None

        json_chapter_list = utils.try_get(
            json_response, 'chapterList', data_type=list, default=[])
        json_chapter = None
        chapter_index = -1
        for (ind, chapter) in enumerate(json_chapter_list):
            if utils.try_get(chapter, 'id') == chapter_id:
                json_chapter = chapter
                chapter_index = ind
                break
        if not json_chapter:
            self.log('build_episode_menu: No chapter ID found \
                for video_id %s' % video_id)
            return

        json_segment_list = utils.try_get(
            json_chapter, 'segmentList', data_type=list, default=[])
        if video_id == chapter_id:
            if include_segments:
                # Generate entries for the whole video and
                # all the segments of this video.
                self.build_entry(json_chapter, banner=banner)

                if audio and chapter_index == 0:
                    for aid in json_chapter_list[1:]:
                        self.build_entry(aid, banner=banner)

                for segment in json_segment_list:
                    self.build_entry(segment, banner=banner)
            else:
                if segment_option and json_segment_list:
                    # Generate a folder for the video
                    self.build_entry(
                        json_chapter, banner=banner, is_folder=True)
                else:
                    # Generate a simple playable item for the video
                    self.build_entry(json_chapter, banner=banner)
        else:
            json_segment = None
            for segment in json_segment_list:
                if utils.try_get(segment, 'id') == segment_id:
                    json_segment = segment
                    break
            if not json_segment:
                self.log('build_episode_menu: No segment ID found \
                    for video_id %s' % video_id)
                return
            # Generate a simple playable item for the video
            self.build_entry(json_segment, banner)

    def build_entry(
            self, json_entry, banner=None, is_folder=False, audio=False,
            fanart=None, urn=None):
        """
        Builds an list item for a video or folder by giving the json part,
        describing this video.

        Keyword arguments:
        json_entry -- the part of the json describing the video
        banner     -- URL of the show's banner (default: None)
        is_folder  -- indicates if the item is a folder (default: False)
        audio      -- boolean value to indicate if the entry contains
        fanart     -- fanart to be used instead of default image
        urn        -- override urn from json_entry
                      audio (default: False)
        """
        self.log('build_entry')
        title = utils.try_get(json_entry, 'title')
        vid = utils.try_get(json_entry, 'id')
        description = utils.try_get(json_entry, 'description')
        lead = utils.try_get(json_entry, 'lead')
        image = utils.try_get(json_entry, 'imageUrl')
        if not urn: urn = utils.try_get(json_entry, 'urn')

        # RTS image links have a strange appendix '/16x9'.
        # This needs to be removed from the URL:
        image = re.sub(r'/\d+x\d+', '', image)

        duration = utils.try_get(
            json_entry, 'duration', data_type=int, default=None)
        if duration:
            duration = duration // 1000
        else:
            duration = utils.get_duration(
                utils.try_get(json_entry, 'duration'))

        date_string = utils.try_get(json_entry, 'date')
        dto = utils.parse_datetime(date_string)
        kodi_date_string = dto.strftime('%Y-%m-%d') if dto else None

        list_item = xbmcgui.ListItem(label=title)
        list_item.setInfo(
            'video',
            {
                'title': title,
                'plot': description or lead,
                'plotoutline': lead,
                'duration': duration,
                'aired': kodi_date_string,
            }
        )

        if not fanart:
            fanart = image

        list_item.setArt({
            'thumb': image,
            'poster': image,
            'fanart' : fanart,
            'banner': banner,
        })

        if not audio:
            subs = utils.try_get(
                json_entry, 'subtitleList', data_type=list, default=[])
            if subs and self.subtitles:
                subtitle_list = [
                    utils.try_get(x, 'url') for x in subs
                    if utils.try_get(x, 'format') == 'VTT']
                if subtitle_list:
                    list_item.setSubtitles(subtitle_list)
                else:
                    self.log(
                        'No WEBVTT subtitles found for video id %s.' % vid)

        # Prefer urn over vid as it contains already all data 
        # (bu, media type, id) and will be used anyway for the stream lookup
        name = urn if urn else vid

        if is_folder:
            list_item.setProperty('IsPlayable', 'false')
            # TODO: check if something needs to be done for audio entries
            url = self.build_url(mode=21, name=name)
        else:
            list_item.setProperty('IsPlayable', 'true')
            url = self.build_url(mode=50, name=name)
        xbmcplugin.addDirectoryItem(
            self.handle, url, list_item, isFolder=is_folder)

    def build_dates_overview_menu(self):
        """
        Builds the menu containing the folders for episodes of
        the last 10 days.
        """
        self.log('build_dates_overview_menu')

        def folder_name(dato):
            """
            Generates a Kodi folder name from an date object.

            Keyword arguments:
            dato -- a date object
            """
            weekdays = (
                self.language(30060),  # Monday
                self.language(30061),  # Tuesday
                self.language(30062),  # Wednesday
                self.language(30063),  # Thursday
                self.language(30064),  # Friday
                self.language(30065),  # Saturday
                self.language(30066)   # Sunday
            )
            today = datetime.date.today()
            if dato == today:
                name = self.language(30058)  # Today
            elif dato == today + datetime.timedelta(-1):
                name = self.language(30059)  # Yesterday
            else:
                name = '%s, %s' % (weekdays[dato.weekday()],
                                   dato.strftime('%d.%m.%Y'))
            return name

        current_date = datetime.date.today()
        number_of_days = 7

        for i in range(number_of_days):
            dato = current_date + datetime.timedelta(-i)
            list_item = xbmcgui.ListItem(label=folder_name(dato))
            list_item.setArt({'thumb': self.icon, 'fanart': self.fanart})
            name = dato.strftime('%d-%m-%Y')
            purl = self.build_url(mode=24, name=name)
            xbmcplugin.addDirectoryItem(
                handle=self.handle, url=purl,
                listitem=list_item, isFolder=True)

        choose_item = xbmcgui.ListItem(label=LANGUAGE(30071))  # Choose date
        choose_item.setArt({'thumb': self.icon, 'fanart': self.fanart})
        purl = self.build_url(mode=25)
        xbmcplugin.addDirectoryItem(
            handle=self.handle, url=purl,
            listitem=choose_item, isFolder=True)

    def pick_date(self):
        """
        Opens a date choosing dialog and lets the user input a date.
        Redirects to the date menu of the chosen date.
        In case of failure or abortion redirects to the date
        overview menu.
        """
        date_picker = xbmcgui.Dialog().numeric(
            1, LANGUAGE(30071), None)  # Choose date
        if date_picker is not None:
            date_elems = date_picker.split('/')
            try:
                day = int(date_elems[0])
                month = int(date_elems[1])
                year = int(date_elems[2])
                chosen_date = datetime.date(year, month, day)
                name = chosen_date.strftime('%d-%m-%Y')
                self.build_date_menu(name)
            except (ValueError, IndexError):
                self.log('pick_date: Invalid date chosen.')
                self.build_dates_overview_menu()
        else:
            self.build_dates_overview_menu()

    def build_date_menu(self, date_string):
        """
        Builds a list of episodes of a given date.

        Keyword arguments:
        date_string -- a string representing date in the form %d-%m-%Y,
                       e.g. 12-03-2017
        """
        self.log('build_date_menu, date_string = %s' % date_string)

        if self.apiv3_url:
            # API v3 use the date in sortable format, i.e. year first
            elems = date_string.split('-')
            query = 'videos-by-date/%s-%s-%s' % (elems[2], elems[1], elems[0])
            return self.build_menu_apiv3(query, 0, segment_option=self.segments)

        url = self.host_url + '/play/tv/programDay/%s' % date_string
        id_list = self.extract_id_list(url)

        for vid in id_list:
            self.build_episode_menu(
                vid, include_segments=False,
                segment_option=self.segments)

    def build_search_menu(self, audio=False):
        """
        Builds a menu for searches.

        Keyword arguments:
        audio  -- Indicates whether audios shall be searched
                  (default: False).
        """
        self.log('build_search_menu, audio = %s' % audio)
        items = [
            {
                # 'Search videos' or 'Search audios'
                'name': LANGUAGE(30112) if not audio else LANGUAGE(30113),
                'mode': 28,
                'show': True,
                'icon': self.icon,
            }, {
                # 'Recently searched videos' or 'Recently searched audios'
                'name': LANGUAGE(30116) if not audio else LANGUAGE(30117),
                'mode': 70,
                'show': True,
                'icon': self.icon,
            }, {
                'name': LANGUAGE(30114),  # 'Search shows'
                'mode': 29,
                'show': True,
                'icon': self.icon,
            }, {
                'name': LANGUAGE(30118),  # 'Recently searched shows'
                'mode': 71,
                'show': True,
                'icon': self.icon,
            }
        ]
        for item in items:
            if not item['show']:
                continue
            list_item = xbmcgui.ListItem(label=item['name'])
            list_item.setProperty('IsPlayable', 'false')
            list_item.setArt({'thumb': item['icon'], 'fanart': self.fanart})
            url = self.build_url(item['mode'])
            xbmcplugin.addDirectoryItem(
                handle=self.handle, url=url, listitem=list_item, isFolder=True)

    def build_recent_search_menu(self, show_or_media, audio=False):
        """
        Lists folders for the most recent searches.

        Keyword arguments:
        show_or_media  -- either 'show' or 'media'
        audio          -- search for audios (default: False)
        """
        self.log(
            'build_recent_search_menu, show_or_media = %s, audio = %s' % (
                show_or_media, audio))
        if show_or_media not in ('show', 'media'):
            self.log(('build_recent_search_menu: `show_or_media` must '
                      'be either \'show\' or \'media\''))
            return
        if show_or_media == 'show':
            filename = RECENT_SHOW_SEARCHES_FILENAME
        else:
            filename = RECENT_MEDIA_SEARCHES_FILENAME
        recent_searches = self.read_searches(filename)
        mode = 29 if show_or_media == 'show' else 28
        for search in recent_searches:
            list_item = xbmcgui.ListItem(label=search)
            list_item.setProperty('IsPlayable', 'false')
            list_item.setArt({'thumb': self.icon})
            url = self.build_url(mode=mode, name=search)
            xbmcplugin.addDirectoryItem(
                handle=self.handle, url=url, listitem=list_item, isFolder=True)

    def build_search_media_menu(self, mode=28, name='', page=1,
                                page_hash='', audio=False):
        """
        Sets up a search for media. If called without name, a dialog will
        show up for a search input. Then the search will be performed and
        the results will be shown in a menu.

        Keyword arguments:
        mode       -- the plugins mode (default: 28)
        name       -- the search name (default: '')
        page       -- the page number (default: 1)
        page_hash  -- the page hash when coming from a previous page
                      (default: '')
        audio      -- boolean value to search for audios instead of
                      videos (default: False)
        """
        self.log(('build_search_media_menu, mode = %s, name = %s, page = %s'
                  ', page_hash = %s, audio = %s') % (mode, name, page,
                                                     page_hash, audio))
        media_type = 'audio' if audio else 'video'
        url_layout = self.host_url + ('/play/search/media?searchQuery=%s'
                                      '&numberOfMedias=%s&mediaType=%s'
                                      '&includeAggregations=false')
        if name:
            # `name` is provided by `next_page` folder or
            # by previously performed search
            query_string = name
            if page_hash:
                # `name` is provided by `next_page` folder, so it is
                # already quoted
                query_url = (url_layout + '&nextPageHash=%s') % (
                    query_string, self.number_of_episodes, media_type,
                    page_hash)
            else:
                # `name` is provided by previously performed search, so it
                # needs to be processed first
                if utils.is_python_2():
                    query_string = query_string.encode('utf8')
                query_string = quote_plus(query_string)
                query_url = url_layout % (
                    name, self.number_of_episodes, media_type)
                query = 'search/media?searchTerm=' + query_string
        else:
            dialog = xbmcgui.Dialog()
            query_string = dialog.input(LANGUAGE(30115))
            if not query_string:
                self.log('build_search_media_menu: No input provided')
                return
            if utils.is_python_2():
                query_string = query_string.encode('utf8')
            if True:
                self.write_search(RECENT_MEDIA_SEARCHES_FILENAME, query_string)
            query_string = quote_plus(query_string)
            query_url = url_layout % (
                query_string, self.number_of_episodes, media_type)
            query = 'search/media?searchTerm=' + query_string

        if self.apiv3_url:
            query = query + '&mediaType=' + media_type + '&includeAggregations=false'
            cursor = page_hash if page_hash else ''
            return self.build_menu_apiv3(query, mode, page_hash=cursor,
                                         name=query_string)
            
        result = json.loads(self.open_url(query_url, use_cache=False))
        media_ids = [
            m['id'] for m in utils.try_get(
                result, 'media', data_type=list,
                default=[]) if utils.try_get(m, 'id')]
        for media_id in media_ids:
            self.build_episode_menu(media_id, audio=audio)
        next_page_hash = utils.try_get(result, 'nextPageHash')
        if next_page_hash and page_hash != next_page_hash:
            next_item = xbmcgui.ListItem(label='>> ' + LANGUAGE(30073))
            next_item.setProperty('IsPlayable', 'false')
            next_item.setArt({
                'thumb': self.icon,
            })
            try:
                page = int(page)
            except TypeError:
                page = 1
            nurl = self.build_url(
                mode=mode, name=query_string,
                page_hash=next_page_hash, page=page+1)
            xbmcplugin.addDirectoryItem(
                self.handle, nurl, next_item, isFolder=True)

    def build_search_show_menu(self, name='', audio=False):
        """
        Peforms a search for shows.

        Keyword arguments:
        name   -- search query (default: '')
        audio  -- boolean; if set, audio shows will be searched, otherwise
                  video shows (default: False)
        """
        self.log(
            'build_search_show_menu, name = %s, audio = %s' % (name, audio))
        url_layout = self.host_url + '/play/search/shows?searchQuery=%s'
        if name:
            query_string = name
            if utils.is_python_2():
                query_string = query_string.encode('utf8')
        else:
            dialog = xbmcgui.Dialog()
            query_string = dialog.input(LANGUAGE(30115))
            if not query_string:
                self.log('build_search_show_menu: No input provided')
                return
            if utils.is_python_2():
                query_string = query_string.encode('utf8')
            if True:
                self.write_search(RECENT_SHOW_SEARCHES_FILENAME, query_string)
        query_string = quote_plus(query_string)
        radio_tv = 'radio' if audio else 'tv'

        if self.apiv3_url:
            url = self.apiv3_url + 'search/shows?searchTerm=' + query_string
            data = json.loads(self.open_url(url, use_cache=False))
            indicator = ':radio:' if audio else ':tv:'
            try:
                for show in data['data']['results']:
                    if indicator in show['urn']:
                        self.build_show_folder(show['id'], radio_tv)
            except: pass
            return
        
        query_url = url_layout % query_string
        result = json.loads(self.open_url(query_url, use_cache=False))
        indicator = ':radio:' if audio else ':tv:'
        show_ids = [m['id'] for m in utils.try_get(
            result, 'shows', data_type=list, default=[]) if (
                utils.try_get(m, 'id') and
                indicator in utils.try_get(m, 'urn'))]
        for show_id in show_ids:
            self.build_show_folder(show_id, radio_tv)

    def get_auth_url(self, url, segment_data=None):
        """
        Returns the authenticated URL from a given stream URL.

        Keyword arguments:
        url -- a given stream URL
        """
        self.log('get_auth_url, url = %s' % url)
        # spl = urlparse.urlparse(url).path.split('/')
        spl = urlps(url).path.split('/')
        token = json.loads(
            self.open_url(
                'http://tp.srgssr.ch/akahd/token?acl=/%s/%s/*' %
                (spl[1], spl[2]), use_cache=False)) or {}
        auth_params = token.get('token', {}).get('authparams')
        if segment_data:
            # timestep_string = self._get_timestep_token(segment_data)
            # url += ('?' if '?' not in url else '&') + timestep_string
            pass
        if auth_params:
            url += ('?' if '?' not in url else '&') + auth_params
        return url

    def play_video(self, video_id, audio=False):
        """
        Gets the stream information starts to play it.

        Keyword arguments:
        video_id -- the urn or id of the video to play
        audio    -- boolean value to indicate if the content is
                    audio (default: False)
        """
        if video_id.startswith('urn:'): urn = video_id
        else:
            media_type = 'audio' if audio else 'video'
            urn = 'urn:' + self.bu + ':' + media_type + ':' + video_id
        self.log('play_video, urn = ' + urn)

        detail_url = ('https://il.srgssr.ch/integrationlayer/2.0/'
                      'mediaComposition/byUrn/' + urn)
        json_response = json.loads(self.open_url(detail_url))
        chapter_list = utils.try_get(
            json_response, 'chapterList', data_type=list, default=[])
        if not chapter_list:
            self.log('play_video: no stream URL found (chapterList empty).')
            return

        first_chapter = utils.try_get(
            chapter_list, 0, data_type=dict, default={})
        chapter = next(
            (e for e in chapter_list if e.get('id') == video_id),
            first_chapter)
        resource_list = utils.try_get(
            chapter, 'resourceList', data_type=list, default=[])
        if not resource_list:
            self.log('play_video: no stream URL found. (resourceList empty)')
            return

        if not audio:
            if 'mediaType' in chapter:
                if chapter['mediaType'].upper() == 'AUDIO': audio = True

        if audio: protocols = {'HTTP': '', 'HTTPS': '', 'HTTP-MP3-STREAM': ''}
        else:     protocols = {'HLS': 'hls', 'DASH': 'mpd' }

        if self.prefer_hd: weight = { 'SD' : 1, 'HD' : 2, 'HQ' : 2 }
        else:              weight = { 'HD' : 1, 'HQ' : 1, 'SD' : 2 }

        current_weight = -1
        stream_url = None
        lic_url = None

        for res in resource_list:
            try:    res_mf_type = protocols[res['protocol'].upper()]
            except: continue

            try:    res_weight = weight[res['quality']]
            except: res_weight = 0
            if res_weight < current_weight: continue

            if 'drmList' in res:
                for drm in res['drmList']:
                    if drm['type'] == 'WIDEVINE':
                        current_weight = res_weight
                        mf_type        = res_mf_type
                        lic_url        = drm['licenseUrl']
                        lic_type       = 'com.widevine.alpha'
                        stream_url     = res['url']
            else:
                current_weight = res_weight
                mf_type        = res_mf_type
                lic_url        = None
                stream_url     = res['url']

        if not stream_url:
            self.log('play_video: no stream URL found.')
            return

        self.log('play_video, stream_url = %s' % stream_url)

        try:    title    = json_response['episode']['title']
        except: title = urn

        auth_url = self.get_auth_url(stream_url)
        if audio:
            play_item =xbmcgui.ListItem(title, path=stream_url)
            xbmcplugin.setResolvedUrl(self.handle, True, play_item) 
            return

        start_time = end_time = None
        if utils.try_get(json_response, 'segmentUrn'):
            segment_list = utils.try_get(
                chapter, 'segmentList', data_type=list, default=[])
            for segment in segment_list:
                if utils.try_get(segment, 'id') == video_id:
                    start_time = utils.try_get(
                        segment, 'markIn', data_type=int, default=None)
                    if start_time:
                        start_time = start_time // 1000
                    end_time = utils.try_get(
                        segment, 'markOut', data_type=int, default=None)
                    if end_time:
                        end_time = end_time // 1000
                    break

            if start_time and end_time:
                parsed_url = urlps(auth_url)
                query_list = parse_qsl(parsed_url.query)
                updated_query_list = []
                for query in query_list:
                    if query[0] == 'start' or query[0] == 'end':
                        continue
                    updated_query_list.append(query)
                updated_query_list.append(
                    ('start', utils.CompatStr(start_time)))
                updated_query_list.append(
                    ('end', utils.CompatStr(end_time)))
                new_query = utils.assemble_query_string(updated_query_list)
                surl_result = ParseResult(
                    parsed_url.scheme, parsed_url.netloc,
                    parsed_url.path, parsed_url.params,
                    new_query, parsed_url.fragment)
                auth_url = surl_result.geturl()
        self.log('play_video, auth_url = %s' % auth_url)
        play_item = xbmcgui.ListItem(title, path=auth_url)
        if self.subtitles:
            subs = self.get_subtitles(stream_url, urn)
            if subs:
                play_item.setSubtitles(subs)

        # Try to use inputstream adaptive
        inp = 'inputstream' if KODI_VERSION >= 19 else 'inputstreamaddon'
        ia  = 'inputstream.adaptive'
        play_item.setProperty(inp, ia)
        play_item.setProperty(ia + '.manifest_type', mf_type)

        if lic_url:
            header  = 'Content-Type=application/octet-stream'
            lic_key = lic_url + '|' + header + '|R{SSM}|'
            play_item.setProperty(ia + '.license_type', lic_type)
            play_item.setProperty(ia + '.license_key',  lic_key)
            play_item.setProperty(ia + '.license_flags', 'persistent_storage')
            play_item.setProperty(ia + '.manifest_update_parameter', 'full')

        xbmcplugin.setResolvedUrl(self.handle, True, play_item)

    def get_subtitles(self, url, name):
        """
        Returns subtitles from an url
        Kodi does not accept m3u playlists for subtitles
        In this case a temporary with all chunks is built

        Keyword arguments:
        url      -- url with subtitle location
        name     -- name of temporary file if required
        """
        webvttbaseurl = None
        caption = None

        parsed_url = urlps(url)
        query_list = parse_qsl(parsed_url.query)
        for query in query_list:
            if query[0] == 'caption':         caption = query[1]
            elif query[0] == 'webvttbaseurl': webvttbaseurl = query[1]
        
        if not caption or not webvttbaseurl: return None
            
        cap_comps = caption.split(':')
        lang = '.' + cap_comps[1] if len(cap_comps) > 1 else ''
        sub_url = ( 'http://' + webvttbaseurl + '/' + cap_comps[0])
        self.log('subtitle url: ' + sub_url)
        if not sub_url.endswith('.m3u8'): return [sub_url]

        # Build temporary local file in case of m3u playlist
        sub_name = 'special://temp/' + name + lang + '.vtt'
        if not xbmcvfs.exists(sub_name):
            m3u_base = sub_url.rsplit('/', 1)[0]
            m3u = self.open_url(sub_url, use_cache=False)
            sub_file = xbmcvfs.File(sub_name, 'w')
            
            # Concatenate chunks and remove header on subsequent
            first = True
            for line in m3u.splitlines():
                if line.startswith('#'): continue
                subs = self.open_url(m3u_base + '/' + line, use_cache=False)
                if first:
                    sub_file.write(subs)
                    first = False
                else:
                    i = 0
                    while i < len(subs) and not subs[i].isnumeric(): i += 1
                    sub_file.write('\n')
                    sub_file.write(subs[i:])

            sub_file.close()

        return [sub_name]

    def play_livestream(self, stream_url):
        """
        Plays a livestream, given a unauthenticated stream url.

        Keyword arguments:
        stream_url -- the stream url
        """
        auth_url = self.get_auth_url(stream_url)
        play_item = xbmcgui.ListItem('Live', path=auth_url)
        xbmcplugin.setResolvedUrl(self.handle, True, play_item)

    def manage_favourite_shows(self, audio=False):
        """
        Opens a Kodi multiselect dialog to let the user choose
        his/her personal favourite show list.
        """
        if audio:
            show_list = self.extract_shows_information('radio')
        else:
            show_list = self.read_all_available_shows()
        stored_favids = self.read_favourite_show_ids()
        names = [x['title'] for x in show_list]
        ids = [x['id'] for x in show_list]

        preselect_inds = []
        for stored_id in stored_favids:
            try:
                preselect_inds.append(ids.index(stored_id))
            except ValueError:
                pass
        ancient_ids = [x for x in stored_favids if x not in ids]

        dialog = xbmcgui.Dialog()
        # Choose your favourite shows
        selected_inds = dialog.multiselect(
            LANGUAGE(30069), names, preselect=preselect_inds)

        if selected_inds is not None:
            new_favids = [ids[ind] for ind in selected_inds]
            # Keep the old show ids:
            new_favids += ancient_ids

            self.write_favourite_show_ids(new_favids)

    def read_favourite_show_ids(self):
        """
        Reads the show ids from the file defined by the global
        variable FAVOURITE_SHOWS_FILENAMES and returns a list
        containing these ids.
        An empty list will be returned in case of failure.
        """
        path = xbmc.translatePath(
            self.real_settings.getAddonInfo('profile'))
        file_path = os.path.join(path, FAVOURITE_SHOWS_FILENAME)
        try:
            with open(file_path, 'r') as f:
                json_file = json.load(f)
                try:
                    return [entry['id'] for entry in json_file]
                except KeyError:
                    self.log('Unexpected file structure for %s.' %
                             FAVOURITE_SHOWS_FILENAME)
                    return []
        except (IOError, TypeError):
            return []

    def write_favourite_show_ids(self, show_ids):
        """
        Writes a list of show ids to the file defined by the global
        variable FAVOURITE_SHOWS_FILENAME.

        Keyword arguments:
        show_ids -- a list of show ids (as strings)
        """
        show_ids_dict_list = [{'id': show_id} for show_id in show_ids]
        path = xbmc.translatePath(
            self.real_settings.getAddonInfo('profile'))
        file_path = os.path.join(path, FAVOURITE_SHOWS_FILENAME)
        if not os.path.exists(path):
            os.makedirs(path)
        with open(file_path, 'w') as f:
            json.dump(show_ids_dict_list, f)

    def read_searches(self, filename):
        path = xbmc.translatePath(self.real_settings.getAddonInfo('profile'))
        file_path = os.path.join(path, filename)
        try:
            with open(file_path, 'r') as f:
                json_file = json.load(f)
            try:
                return[entry['search'] for entry in json_file]
            except KeyError:
                self.log('Unexpected file structure for %s.' %
                         filename)
                return []
        except (IOError, TypeError):
            return []

    def write_search(self, filename, name, max_entries=10):
        searches = self.read_searches(filename)
        try:
            searches.remove(name)
        except ValueError:
            pass
        if len(searches) >= max_entries:
            searches.pop()
        searches.insert(0, name)
        write_dict_list = [{'search': entry} for entry in searches]
        path = xbmc.translatePath(self.real_settings.getAddonInfo('profile'))
        file_path = os.path.join(path, filename)
        if not os.path.exists(path):
            os.makedirs(path)
        with open(file_path, 'w') as f:
            json.dump(write_dict_list, f)

    # Live TV is currently not supported due to recently added DRM protection:
    #
    # https://www.srf.ch/sendungen/hallosrf/weshalb-funktioniert-der-livestream-auf-srf-ch-nicht-mehr
    # https://rtsr.ch/digitalrightsmanagement/
    # https://www.rsi.ch/chi-siamo/mestieri/La-SSR-introduce-la-codifica-digitale-11038056.html
    #
    #
    # def build_tv_menu(self):
    #     """
    #     Builds the overview over the TV channels.
    #     """
    #     overview_url = '%s/play/tv/live/overview' % self.host_url
    #     overview_json = json.loads(
    #         self.open_url(overview_url, use_cache=False))
    #     urns = [utils.try_get(x, 'urn') for x in utils.try_get(
    #         overview_json, 'teaser', data_type=list, default=[])
    #         if utils.try_get(x, 'urn')]
    #     for urn in urns:
    #         json_url = ('https://il.srgssr.ch/integrationlayer/2.0/'
    #                     'mediaComposition/byUrn/%s.json') % urn
    #         info_json = json.loads(self.open_url(json_url, use_cache=False))
    #         json_entry = utils.try_get(
    #             info_json, ('chapterList', 0), data_type=dict, default={})
    #         if not json_entry:
    #             self.log('build_tv_menu: Unexpected json structure '
    #                      'for element %s' % urn)
    #             continue
    #         self.build_entry(json_entry)

    def build_live_menu(self, extract_srf3=False):
        """
        Builds the menu listing the currently available livestreams.
        """
        def add_stream_by_urn(urn):
            detail_url = ('https://il.srgssr.ch/integrationlayer/2.0/'
                          'mediaComposition/byUrn/' + urn)
            json_response = json.loads(self.open_url(detail_url))
            details = json.loads(self.open_url(detail_url))
            if 'channel' in details:
                self.build_entry(details['channel'],
                                 fanart=self.fanart, urn=urn)

        if self.apiv3_url:
            # TV Channels
            tv = json.loads(self.open_url(self.apiv3_url + 'tv-livestreams'))
            if tv and 'data' in tv:
                for channel in tv['data']:
                    if 'livestreamUrn' in channel:
                        add_stream_by_urn(channel['livestreamUrn'])
            
            # Radio channles
            radio = json.loads(self.open_url(self.apiv3_url + 'radio/channels'))
            if radio and 'data' in radio and 'channels' in radio['data']:
                for channel in radio['data']['channels']:
                    self.log('channel ' + channel['title'])
                    if 'livestreams' in channel:
                        for stream in channel['livestreams']:
                            if 'livestreamUrn' in stream:
                                add_stream_by_urn(stream['livestreamUrn'])
                                break

            return

        def get_live_ids():
            """
            Downloads the main webpage and scrapes it for
            possible livestreams. If some live events were found, a list
            of live ids will be returned, otherwise an empty list.
            """
            live_ids = []
            webpage = self.open_url(self.host_url, use_cache=False)
            event_id_regex = r'(?:data-sport-id=\"|eventId=)(?P<live_id>\d+)'
            try:
                for match in re.finditer(event_id_regex, webpage):
                    live_ids.append(match.group('live_id'))
            except StopIteration:
                pass
            return live_ids

        def get_srf3_live_ids():
            """
            Returns a list of Radio SRF 3 video streams.
            """
            url = 'https://www.srf.ch/radio-srf-3'
            webpage = self.open_url(url, use_cache=False)
            video_id_regex = r'''(?x)
                                   popupvideoplayer\?id=
                                   (?P<video_id>
                                       [a-f0-9]{8}-
                                       [a-f0-9]{4}-
                                       [a-f0-9]{4}-
                                       [a-f0-9]{4}-
                                       [a-f0-9]{12}
                                    )
                                '''
            live_ids = []
            try:
                for match in re.finditer(video_id_regex, webpage):
                    live_ids.append(match.group('video_id'))
            except StopIteration:
                pass
            return live_ids
        live_ids = get_live_ids()
        for lid in live_ids:
            api_url = ('https://event.api.swisstxt.ch/v1/events/'
                       '%s/byEventItemId/?eids=%s') % (self.bu, lid)
            live_json = json.loads(self.open_url(api_url))
            entry = utils.try_get(live_json, 0, data_type=dict, default={})
            if not entry:
                self.log('build_live_menu: No entry found '
                         'for live id %s.' % lid)
                continue
            if utils.try_get(entry, 'streamType') == 'noStream':
                continue
            title = utils.try_get(entry, 'title')
            stream_url = utils.try_get(entry, 'hls')
            image = utils.try_get(entry, 'imageUrl')
            item = xbmcgui.ListItem(label=title)
            item.setProperty('IsPlayable', 'true')
            item.setArt({'thumb': image})
            purl = self.build_url(mode=51, name=stream_url)
            xbmcplugin.addDirectoryItem(
                self.handle, purl, item, isFolder=False)
        if extract_srf3:
            srf3_ids = get_srf3_live_ids()
            for vid in srf3_ids:
                self.build_episode_menu(vid, include_segments=False)

    def get_radio_channels(self):
        """
        Gets all the radio channels which have content in the media library.
        It returns a list of dictionaries, containing the channel id (key: id)
        and the name of the channel (key: name).

        Keyword arguments:
        raw  -- boolean value; if set, the method returns the parsed requested
                json instead of the simplified data (default: False)
        """
        self.log('get_radio_channels')
        cache_id = self.addon_id + '.radio_channels'
        # channels = self.cache.get(cache_id)
        channels = []
        if channels:
            return channels

        url = '%s/play/radio/live/overview' % self.host_url
        channel_json = json.loads(self.open_url(url))
        channel_list = utils.try_get(
            channel_json, 'overview', data_type=list, default=[])

        channels = []
        for ch in channel_list:
            name = utils.try_get(ch, 'name')
            # channel_id = utils.try_get(
            #     ch, 'id') or utils.try_get(ch, 'channelId')
            id = utils.try_get(ch, 'id')
            channel_id = utils.try_get(ch, 'channelId')
            if not (id and channel_id and name):
                continue
            url = ('https://il.srgssr.ch/integrationlayer/2.0/%s/'
                   'mediaComposition/audio/%s.json') % (self.bu, id)

            # TODO: error handling
            detailed_content = json.loads(self.open_url(url))
            image = utils.try_get(
                detailed_content, ('episode', 'imageUrl')) or utils.try_get(
                detailed_content, ('show', 'imageUrl')) or utils.try_get(
                detailed_content, ('channel', 'imageUrl'))
            image = re.sub(r'/\d+x\d+$', '', image)  # needed for RTS
            channels.append({
                'name': name,
                'id': id,
                'channelId': channel_id,
                'image': image,
            })
        self.cache.set(
            cache_id, channels, expiration=datetime.timedelta(days=1))
        return channels

    def get_live_radio_channels(self):
        """
        Tries to get the direct stream urls of the three radio channels
        Radio Swiss Pop, Radio Swiss Classic and Radio Swiss Jazz. If the
        stream url can be found, the radio channel dictionary (keys are
        'name', 'url', 'image', 'stream') will be appended to the list
        which will be returned at the end.
        """
        uri = ('special://home/addons/%s/resources/media') % ADDON_ID
        lang = 'de'
        if self.bu == 'rts':
            lang = 'fr'
        elif self.bu == 'rsi':
            lang = 'it'
        radio_info = [
            {
                'name': 'Radio Swiss Pop',
                'url': 'http://www.radioswisspop.ch/%s' % lang,
                'image': os.path.join(
                    xbmc.translatePath(uri), 'icon_radioswisspop.png'),
            }, {
                'name': 'Radio Swiss Classic',
                'url': 'http://www.radioswissclassic.ch/%s' % lang,
                'image': os.path.join(
                    xbmc.translatePath(uri), 'icon_radioswissclassic.png'),
            }, {
                'name': 'Radio Swiss Jazz',
                'url': 'http://www.radioswissjazz.ch/%s' % lang,
                'image': os.path.join(
                    xbmc.translatePath(uri), 'icon_radioswissjazz.png'),
            }]
        live_radio_list = []
        regex = r'title\s*:\s*"[^"]+?".+?mp3\s*:\s*"(?P<stream>[^"]+?)"'
        for info in radio_info:
            try:
                webpage = self.open_url(info['url'])
            except Exception:
                self.log('get_live_radio_channels: Unable to open '
                         'webpage %s' % info['url'])
                continue
            match = re.search(regex, webpage)
            if not match:
                self.log('get_live_radio_channels: Unable to extract stream '
                         'for %s' % info['name'])
                continue
            info.update({
                'stream': match.group('stream')
            })
            live_radio_list.append(info)
        return live_radio_list

    def build_radio_channels_menu(self):
        """
        Builds a menu containing folders of the available radio channels which
        have content in the media library.
        """
        self.log('build_radio_channels_menu')
        channels = self.get_radio_channels()
        for ch in channels:
            list_item = xbmcgui.ListItem(label=ch['name'])
            list_item.setProperty('IsPlayable', 'false')
            list_item.setArt({
                'thumb': ch['image'],
            })
            purl = self.build_url(41, name=ch['channelId'])
            xbmcplugin.addDirectoryItem(
                self.handle, purl, list_item, isFolder=True)

    def build_radio_channel_overview(self, channel_id):
        """
        Builds the overview menu of a given radio channel.

        Keyword arguments:
        channel_id  -- the channel id of the given radio channel
        """
        self.log('build_radio_channel_overview')
        thumbnail = next((
            e['image'] for e in self.get_radio_channels()
            if e['channelId'] == channel_id), '')
        menu_list = [
            {
                'identifier': 'Shows',
                'name': self.plugin_language(30080),
                'icon': thumbnail,
                'purl': {
                    'name': channel_id,
                    'mode': 42,
                },
            }, {
                'identifier': 'Newest_Audios',
                'name': self.plugin_language(30076),
                'icon': thumbnail,
                'purl': {
                    'name': channel_id,
                    'mode': 43,
                },
            }, {
                'identifier': 'Most_Listened',
                'name': self.plugin_language(30077),
                'icon': thumbnail,
                'purl': {
                    'name': channel_id,
                    'mode': 44,
                },
            }
        ]
        self.build_folder_menu(menu_list)

    def build_audio_menu(self, playlist, mode, channel_id=None, page=1):
        """
        Builds a menu containing audio items.

        Keyword arguments:
        playlist    -- either 'Newest' for the latest available audios
                       or 'Most clicked' for the most clicked audios
        mode        -- the plugin url mode to use for the next page item
        channel_id  -- the channel id of a radio channel if the request
                       is intended to be for a specific radio channel,
                       otherwise use None (default: None)
        page        -- the page number to display (default: 1)
        """
        self.log('build_audio_menu, playlist = %s, mode = %s, channel_id = %s,'
                 ' page = %s' % (playlist, mode, channel_id, page))
        number_of_audios = 50
        if playlist == 'Newest':
            ptype = 'latest'
        elif playlist == 'Most clicked':
            ptype = 'mostclicked'
        else:
            self.log('build_audio_menu: Invalid playlist type.')
        url = '%s/play/radio/%s/audios?numberOfAudios=%s' % (
            self.host_url, ptype, number_of_audios)
        if channel_id:
            char = '?' if '?' not in url else '&'
            url += '%schannelId=%s' % (char, channel_id)

        # TODO: Code duplication: Copied from above
        id_list = self.extract_id_list(url)
        try:
            page = int(page)
        except TypeError:
            page = 1

        reduced_id_list = id_list[(page - 1) * self.number_of_episodes:
                                  page * self.number_of_episodes]
        for vid in reduced_id_list:
            self.build_episode_menu(
                vid, include_segments=False,
                segment_option=self.segments_topics, audio=True)

        try:
            vid = id_list[page*self.number_of_episodes]
            next_item = xbmcgui.ListItem(
                label='>> ' + LANGUAGE(30073))  # Next page
            next_item.setProperty('IsPlayable', 'false')
            name = channel_id
            purl = self.build_url(mode=mode, name=name, page=page+1)
            xbmcplugin.addDirectoryItem(
                handle=self.handle, url=purl,
                listitem=next_item, isFolder=True)
        except IndexError:
            return

    def parse_embedded_json(self, url, regex):
        """
        Parses embedded json content from a webpage.

        Keyword arguments:
        url    -- the url of the webpage to load
        regex  -- a regular expression containing a subgroup for the
                  embedded json
        """
        self.log('parse_embedded_json: url = %s, regex = %s' % (url, regex))
        webpage = self.open_url(url)
        match = re.search(regex, webpage, re.DOTALL)
        if not match:
            self.log('parse_embedded_json: Unable to find regular expression')
            return {}
        data = match.group(1).replace('&quot;', '"').replace('&amp;', '&')
        try:
            parsed = json.loads(data, strict=False)
        except Exception:
            self.log('parse_embedded_json: Unable to parse json')
            parsed = {}
        return parsed

    def extract_shows_information(self, radio_tv, channel_id=None):
        """
        Extracts the relevant information (like show id, title, description,
        etc.) for the featured shows. This information is returned as a list
        of dictionaries.

        Keyword arguments:
        radio_tv    -- either 'radio' for radio shows or 'tv' for tv shows
        channel_id  -- a channel id, if it is desired the extract the
                       information for a given channel, otherwise use None
                       (default: None)
        """
        self.log('extract_shows_information, radio_tv = %s,'
                 ' channel_id = %s' % (radio_tv, channel_id))
        if radio_tv not in ('radio', 'tv'):
            self.log('extract_show_information: Invalid value for radio_tv')
            return

        # It is not possible to get all the radio shows by dumping
        # the channel id. In this case we need to make seperate requests
        # for every radio channel and merge the results:
        if radio_tv == 'radio' and not channel_id:
            channels = self.get_radio_channels()
            # TODO: In the future, this should be done by multiprocessing
            # for the platforms that support it
            channel_shows = [
                self.extract_shows_information(
                    radio_tv, channel_id=channel['channelId']
                    ) for channel in channels]
            return sorted(utils.generate_unique_list(
                channel_shows, 'id'), key=lambda k: k['title'].lower())

        url = '%s/play/%s/shows/alphabetical-sections' % (
            self.host_url, radio_tv)
        if channel_id:
            char = '?' if '?' not in url else '&'
            url += '%schannelId=%s' % (char, channel_id)

        json_data = self.parse_embedded_json(
            url, r'data-alphabetical-sections="(.+?)"')

        shows = []
        for entry in json_data:
            show_entries = utils.try_get(
                entry, 'showTeaserList', data_type=list, default=[])
            for se in show_entries:
                aid = utils.try_get(se, 'id')
                if not aid:
                    continue
                shows.append({
                    'id': aid,
                    'title': utils.try_get(se, 'title'),
                    'description': utils.try_get(se, 'desription'),
                    'lead': utils.try_get(se, 'lead'),
                    'imageUrl': re.sub(
                        r'/\d+x\d+$', '', utils.try_get(se, 'imageUrl')),
                    'bannerImageUrl': re.sub(
                        r'/\d+x\d+$', '', utils.try_get(se, 'bannerImageUrl')),
                })
        return shows

    def extract_radio_topics(self):
        """
        Extracts a list of the hosted radio topics. Each entry is a
        dictionary with keys 'title' and 'url'. The url consists
        only of the path.
        """
        self.log('extract_radio_topics')
        url = '%s/play/radio/topic/shows/module' % self.host_url
        json_data = self.parse_embedded_json(url, r'topic\s*in\s*(.+?)"')

        topic_list = []
        for entry in json_data:
            title = utils.try_get(entry, 'title')
            url = utils.try_get(entry, 'url')
            if title and url:
                topic_list.append({
                    'title': title,
                    'url': url,
                })
        return topic_list

    def build_radio_topics_menu(self):
        """
        Builds a menu for the hosted radio topics.
        """
        self.log('build_radio_topics_menu')
        topic_list = self.extract_radio_topics()
        for entry in topic_list:
            list_item = xbmcgui.ListItem(label=entry['title'])
            list_item.setArt({
                'icon': self.icon,
            })
            purl = self.build_url(mode=49, name=entry['url'])
            list_item.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(
                self.handle, purl, list_item, isFolder=True)

    # Only works for SRF:
    def build_radio_shows_by_topic(self, url):
        self.log('build_radio_shows_by_topic, url = %s' % url)
        url = '%s%s' % (self.host_url, url)
        json_content = json.loads(self.open_url(url))
        ids = [utils.try_get(x, 'id') for x in utils.try_get(
            json_content, 'teaser', list, []) if utils.try_get(x, 'id')]
        self.build_shows_menu('radio', favids=ids)

    def build_shows_menu(self, radio_tv, channel_id=None, favids=None):
        """
        Builds a menu of available shows.

        Keyword arguments:
        radio_tv    -- either 'radio' for radio shows or 'tv' for tv shows
        channel_id  -- a channel id, if it is desired to build the show menu
                       for a given channel, otherwise use None
                       (default: None)
        favids      -- a list of show ids; if it is set, only the shows
                       in that list will be included in the menu (provided
                       that the shows still exist in the media library)
                       (default: None)
        """
        self.log('build_shows_menu, radio_tv = %s, channel_id = %s'
                 'favids = %s' % (radio_tv, channel_id, favids))
        if radio_tv not in ('radio', 'tv'):
            self.log('build_shows_menu: Invalid value for radio_tv')
            return

        shows = self.extract_shows_information(radio_tv, channel_id=channel_id)
        if favids is not None:
            shows = [show for show in shows if show['id'] in favids]

        for show in shows:
            list_item = xbmcgui.ListItem(label=show['title'])
            list_item.setProperty('IsPlayable', 'false')
            list_item.setArt({
                'thumb': show['imageUrl'],
                'poster': show['imageUrl'],
                'banner': show['bannerImageUrl'],
            })
            list_item.setInfo(
                'video',
                {
                    'title': show['title'],
                    'plot': show['lead'] or show['description'],
                }
            )
            surl = self.build_url(mode=20, name=show['id'])
            xbmcplugin.addDirectoryItem(
                self.handle, surl, list_item, isFolder=True)

    # TODO: Merge this with build_favourite_shows_menu
    def build_favourite_radio_shows_menu(self):
        self.log('build_favourite_radio_shows_menu')
        favids = self.read_favourite_show_ids()
        self.build_shows_menu('radio', favids=favids)

    def build_live_radio_menu(self, include_live_only=True):
        """
        Builds a Kodi menu for the live radio channels.

        Keyword arguments:
        include_live_only  -- if set, the three radio channels which
                              have not an own media library (Radio Swiss Pop,
                              Radio Swiss Jazz and Radio Swiss Classic) will
                              be included in the list (default: True)
        """
        self.log('build_live_radio_menu')
        channels = self.get_radio_channels()
        channels += self.get_live_radio_channels() if include_live_only else []
        for ch in channels:
            list_item = xbmcgui.ListItem(label=ch['name'])
            list_item.setProperty('IsPlayable', 'true')
            list_item.setInfo('music', {'title': ch['name']})
            list_item.setArt({'thumb': ch['image']})
            try:
                purl = self.build_url(mode=50, name=ch['id'])
            except KeyError:
                purl = self.build_url(mode=51, name=ch['stream'])
            xbmcplugin.addDirectoryItem(
                self.handle, purl, list_item, isFolder=False)

    def _read_youtube_channels(self, fname):
        """
        Reads YouTube channel IDs from a specified file and returns a list
        of these channel IDs.

        Keyword arguments:
        fname  -- the path to the file to be read
        """
        data_file = os.path.join(xbmc.translatePath(self.data_uri), fname)
        with open(data_file, 'r') as f:
            ch_content = json.load(f)
            cids = [elem['channel'] for elem in ch_content.get('channels', [])]
            return cids
        return []

    def get_youtube_channel_ids(self):
        """
        Uses the cache to generate a list of the stored YouTube channel IDs.
        """
        cache_identifier = self.addon_id + '.youtube_channel_ids'
        channel_ids = self.cache.get(cache_identifier)
        if not channel_ids:
            self.log('get_youtube_channel_ids: Caching YouTube channel ids.'
                     'This log message should not appear too many times.')
            channel_ids = self._read_youtube_channels(
                YOUTUBE_CHANNELS_FILENAME)
            self.cache.set(cache_identifier, channel_ids)
        return channel_ids

    def build_youtube_main_menu(self):
        """
        Builds the main YouTube menu.
        """
        items = [{
            'name': LANGUAGE(30110),
            'mode': 31,
        }, {
            'name': LANGUAGE(30111),
            'mode': 32,
        }]

        for item in items:
            list_item = xbmcgui.ListItem(label=item['name'])
            list_item.setProperty('IsPlayable', 'false')
            list_item.setArt({
                'icon': self.get_youtube_icon(),
            })
            purl = self.build_url(mode=item['mode'])
            xbmcplugin.addDirectoryItem(
                self.handle, purl, list_item, isFolder=True)

    def build_youtube_channel_overview_menu(self, mode):
        """
        Builds a menu of folders containing the plugin's
        YouTube channels.

        Keyword arguments:
        channel_ids  -- a list of YouTube channel IDs
        mode         -- the plugin's URL mode
        """
        channel_ids = self.get_youtube_channel_ids()
        plugin_url = self.build_url(mode=mode, name='%s')
        youtube_channels.YoutubeChannels(
            self.handle, channel_ids,
            self.addon_id, self.debug).build_channel_overview_menu(
                plugin_channel_url=plugin_url)

    def build_youtube_channel_menu(self, cid, mode, page=1, page_token=''):
        """
        Builds a YouTube channel menu (containing a list of the
        most recent uploaded videos).

        Keyword arguments:
        channel_ids  -- a list of channel IDs
        cid          -- the channel ID of the channel to display
        mode         -- the number which specifies to trigger this
                        action in the plugin's URL
        page         -- the page number to display (first page
                        starts at 1)
        page_token   -- the page token specifies the token that
                        should be used on the the YouTube API
                        request
        """
        try:
            page = int(page)
        except TypeError:
            page = 1

        channel_ids = self.get_youtube_channel_ids()
        next_page_token = youtube_channels.YoutubeChannels(
            self.handle, channel_ids,
            self.addon_id, self.debug).build_channel_menu(
                cid, page_token=page_token)
        if next_page_token:
            next_item = xbmcgui.ListItem(label='>> ' + LANGUAGE(30073))
            next_url = self.build_url(
                mode=mode, name=cid, page_hash=next_page_token)
            next_item.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(
                self.handle, next_url, next_item, isFolder=True)

    def build_youtube_newest_videos_menu(self, mode, page=1):
        """
        Builds a YouTube menu containing the most recent uploaded
        videos of all the defined channels.

        Keyword arguments:
        channel_ids  -- a list of channel IDs
        mode         -- the mode to be used in the plugin's URL
        page         -- the page number (first page starts at 1)
        """
        try:
            page = int(page)
        except TypeError:
            page = 1

        channel_ids = self.get_youtube_channel_ids()
        next_page = youtube_channels.YoutubeChannels(
            self.handle, channel_ids,
            self.addon_id, self.debug).build_newest_videos(page=page)
        if next_page:
            next_item = xbmcgui.ListItem(label='>> ' + LANGUAGE(30073))
            next_url = self.build_url(mode=mode, page=next_page)
            next_item.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(
                self.handle, next_url, next_item, isFolder=True)
