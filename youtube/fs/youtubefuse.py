#!/usr/bin/env python
__author__      = "Vishal Patil"
__copyright__   = "Copyright 2007 - 2008, Vishal Patil"
__license__     = "MIT"

import logging
import os, sys
import fcntl
import stat
import time
import re
try:
    import _find_fuse_parts
except ImportError:
    pass
import fuse
from fuse import Fuse
from youtube.api.protocol import YoutubeUser
from youtube.api.protocol import YoutubeVideo
from youtube.api.protocol import YoutubePlaylist
from youtube.api.protocol import YoutubeProfile
from youtube.api import gdataTime2UnixTime
from youtube.fs.fsobjects import YoutubeStat
from youtube.fs.fsobjects import YoutubeFSInodeCache
from youtube.fs.fsobjects import YoutubeFSInode
import youtube.fs

class YoutubeFUSE(Fuse):
    def __init__(self, *args, **kw):
        Fuse.__init__(self, *args, **kw)
        self.root = '/'
        self.youtubeUser = None
        self.inodeCache  =  YoutubeFSInodeCache()
        logging.debug("YoutubeFUSE init complete")

    def open(self,path,flags):
        logging.debug("YoutubeFUSE open called on %s with flags %s",\
                path,str(flags))
        if (flags & 3) != os.O_RDONLY:
            logging.info("YoutubeFUSE open error for %s with flags %s",\
                path,str(flags))
            return -errno.EACCES
        return 0

    def read(self,path,size,offset):
        logging.debug("YoutubeFUSE read for %s with size %s offset %s",\
                path,str(size),str(offset))
        inode = self.inodeCache.getInode(path)
        logging.debug("YoutubeFUSE read inode data for %s is %s,%d",path,\
                inode.data,len(inode.data))
        slen = len(inode.data)
        if offset < slen:
            if (offset+size)>slen:
                size = slen-offset
            buf = inode.data[offset:offset+size]
        else:
            buf = ''
        logging.debug("YoutubeFUSE read returning buf %s",buf) 
        return buf
 
    def getattr(self, path):
        logging.debug("YoutubeFUSE getattr for %s",path)
        inode = self.inodeCache.getInode(path)
        if inode == None:
            return None
        logging.debug("YoutubeFUSE getattr for %s is %s and type %s",\
                inode.path,str(inode),type(inode.stat))
        return inode.stat 

    def readdir(self, path, offset):
        dirInode = self.inodeCache.getInode(path)
        for entry in dirInode.children:
            yield fuse.Direntry(entry.direntry.strip('/').encode('ascii'))

    def fsinit(self):
        logging.debug("YoutubeFUSE fsinit " + self.username)
        self.createfs()
        os.chdir(self.root)

    def __addRootInode(self):
        #
        # Added the root directory inode
        #
        mode = stat.S_IFDIR | 0755
        rootDirInode = YoutubeFSInode('/',mode,0,\
            long(time.time()),long(time.time())) 
        self.inodeCache.addInode(rootDirInode)

    def __addProfileInode(self):
        #
        # Add the profile file
        #
        profile = self.youtubeUser.getProfile()
        mode = stat.S_IFREG | 0444
        profileInode = YoutubeFSInode('/profile',mode,\
                0,profile.ctime,profile.mtime)
        profileInode.ctime  = profile.ctime
        profileInode.mtime  = profile.mtime
        profileInode.setData(profile.getData())
        self.inodeCache.addInode(profileInode) 
        rootDirInode = self.inodeCache.getInode('/')
        rootDirInode.addChildInode(profileInode)

    def __addVideos(self,parentPath,videos):
        logging.debug("YoutubeFUSE adding videos inodes to %s",\
            parentPath)
        parentInode =  self.inodeCache.getInode(parentPath)
        for video in videos:
            logging.debug("YoutubeFUSE adding video %s",video.title)
            mode = stat.S_IFREG | 0444
            path = ("%s/%s.%s") % (parentPath,video.title,\
                        youtube.fs.VIDEO_FILE_EXTENSION)
            videoInode =  YoutubeFSInode(path,mode,\
                        video.id,video.ctime,video.mtime)
            videoInode.setData(video.getContents())
            self.inodeCache.addInode(videoInode) 
            parentInode.addChildInode(videoInode)

    def __addFavouritesInode(self):
        #
        # Get the favourite videos
        # 
        favourities = self.youtubeUser.getFavourities()
        mode = stat.S_IFDIR | 0755
        favouritesInode = YoutubeFSInode('/favourites',mode,\
                0,favourities.ctime,favourities.mtime)
        self.inodeCache.addInode(favouritesInode) 
        rootDirInode = self.inodeCache.getInode('/')
        rootDirInode.addChildInode(favouritesInode)
        self.__addVideos("/favourites",favourities.getVideos())       

    def __addPlaylists(self,parent,playlists):
        for playlist in playlists:
            playlistsDirInode = self.inodeCache.getInode(parent)
            plPath = "%s/%s" % (parent,playlist.title) 
            logging.debug("YoutubeFUSE adding playlist %s",plPath)
            mode = stat.S_IFDIR | 0755
            playlistInode = YoutubeFSInode(plPath,mode,\
                0,playlist.ctime,playlist.mtime)
            self.inodeCache.addInode(playlistInode) 
            playlistsDirInode.addChildInode(playlistInode)
            self.__addVideos(plPath,playlist.getVideos())

    def __addSubDir(self,parent,dir):
        regex = re.compile('//')
        mode = stat.S_IFDIR | 0755
        dirPath = "%s/%s" % (parent,dir)
        dirPath = regex.sub('/',dirPath)
        logging.debug("YoutubeFUSE adding subdir %s",dirPath)
        dirInode = YoutubeFSInode(dirPath,mode,0,\
            long(time.time()),long(time.time())) 
        self.inodeCache.addInode(dirInode)
        rootDirInode = self.inodeCache.getInode(parent)
        rootDirInode.addChildInode(dirInode)

    def __addPlaylistInodes(self):
        #
        # Added the playlists directory inode
        #
        self.__addSubDir('/','playlists')       
        self.__addPlaylists('/playlists',self.youtubeUser.getPlaylists())

    def __addSubscriptionInodes(self):
        #
        # Added the subscription directory inode
        #
        self.__addSubDir('/','subscriptions')       
        self.__addPlaylists('/subscriptions',\
                        self.youtubeUser.getSubscriptions())

 
    def createfs(self):
        try:
            logging.debug("YoutubeFUSE createfs")
            self.youtubeUser = YoutubeUser(self.username)

            self.__addRootInode()
            self.__addProfileInode()     
            self.__addFavouritesInode()
            self.__addPlaylistInodes()       
            self.__addSubscriptionInodes()  
 
        except Exception,inst:
            logging.debug("YoutubeFUSE createfs exception : " + str(inst))

    def main(self, *a, **kw):
        return Fuse.main(self, *a, **kw)



