#!/usr/bin/env python

"""
Use Epipolar Geometry to get the depth map from the video containing the sensor_msgs
Calibration obtained separately
"""

import sys, time 
import numpy.core.multiarray
import cv2, cv_bridge
import roslib, rospy
import numpy as np
from matplotlib import pyplot as plt

from sensor_msgs.msg import Image, CompressedImage
from depth_helper import drawlines, drawFundamental

#from multiprocessing import Lock

VERBOSE=True

class ImageDepthMap:
    def __init__(self):
        rospy.init_node("depth_map", anonymous=False)
        # parameters from the ros parameter server
        # self.read_parameters()
        # subscriber
        self.image_sub = rospy.Subscriber('image_decompressed', Image, self.depth_callback)
        print 'subscribed to the image topic'

        # publisher
        # self.moving_pub = rospy.Publisher('depth_map', Image, queue_size=1)

        # timer - processing outside callback ? 
        #self.processing_timer = rospy.Timer(rospy.Duration(0.05), self.processing)

        # cv_bridge
        self.bridge = cv_bridge.CvBridge()
        # class member
        self.isImage_ = False
        self.isNewImage_ = False
        self.isProcessInit_ = False
        self.imageNow_ = None
        self.imagePrev_ = None
        self.imageNowProcess_ = None
        self.imagePrevProcess_ = None
        #self.mutex_ = Lock()

        #parametres intrinseques camera
        self.D = [-0.01, 0.09, 0, 0, 0] #no distorsion

        self.K = np.array([[929.5, 0, 487.4],[ 0, 928, 363], [ 0, 0, 1]])
        self.P = [937.8, 0, 489, 0, 0, 929, 363,0,0,0,1,0]

        self.frame_id = "/tello/camera_front"
        
    def depth_callback(self,ros_data):
        #with self.mutex_:
        try:
            self.imagePrev_ = self.imageNow_.copy()
            self.isImage_ = True
        except AttributeError:
            rospy.logwarn("imageNow_ has no attribute 'copy'. Probably still at init value 'None'")
        # conversion ros image vers opencv
        self.imageNow_ = self.bridge.imgmsg_to_cv2(ros_data,desired_encoding='bgr8')
        self.isNewImage_ = True

        cv2.imshow("flux vid", self.imageNow_)
        #cv2.imshow("debug self.imagePrev_", self.imagePrev_)
        
        #print 'debug prout'
        cv2.waitKey(1)
        
        self.depth_process()


    def depth_process(self): 
        #Conversion en niveaux de gris
        img1 = cv2.cvtColor(self.imageNow_,cv2.COLOR_BGR2GRAY)
        img2 = cv2.cvtColor(self.imagePrev_,cv2.COLOR_BGR2GRAY)

        ###### Detection des points clefs
        kaze = cv2.KAZE_create(upright = False,#Par defaut : false
                            threshold =0.001,#Par defaut : 0.001
                            nOctaves = 4,#Par defaut : 4
                            nOctaveLayers = 4,#Par defaut : 4
                            diffusivity = 2)#Par defaut : 2

        # detection des points KAZE et calcul des descripteurs M-SURF
        kp1, des1 = kaze.detectAndCompute(img1,None)
        kp2, des2 = kaze.detectAndCompute(img2,None)

        print ('Nb de points KAZE : ' + str(len(kp1)) + ' (gauche) ' + str(len(kp2)) + ' (droite)')
        
        imgd=img1
        imgd = cv2.drawKeypoints(img1, kp1, imgd,-1,flags=4)

        #cv2.imshow('keypoint',imgd)
        #cv2.waitKey(1)

        pts1 = []
        pts2 = []

        #Distance L2 pour descripteur M-SURF (KAZE)
        bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
        # Extraction de la liste des 2-plus-proches-voisins
        matches = bf.knnMatch(des1,des2, k=2)
        # Filtrage des appariements par application du ratio test
        good = []
        for m,n in matches: #0.7
            if m.distance < 0.7*n.distance:
                pts2.append(kp2[m.trainIdx].pt)
                pts1.append(kp1[m.queryIdx].pt)
                good.append([m])

        mfilt_image = np.array([])
        draw_params = dict(matchColor = (0,255,0),
                        singlePointColor = (255,0,0),
                        flags = 0)
        mfilt_image = cv2.drawMatchesKnn(img1,kp1,img2,kp2,good,None,**draw_params)
        pts1 = np.float32(pts1)
        pts2 = np.float32(pts2)
        print('Nb de paires selectionnees : ' + str(pts1.shape[0]))

        ###### Calcul de la Matrice Fondamentale avec OpenCV RANSAC
        FRansac, mask = cv2.findFundamentalMat(pts1,pts2,cv2.FM_RANSAC,
                                ransacReprojThreshold = 0.5, # default 0.5 Distance max de reprojection en pixels pour un inlier 
                                confidence = 0.99) # Niveau de confiance desire
        print('Nb inliers RANSAC : ' + str(mask.sum()))

        # on affiche que les inliers
        inlierpts1 = pts1[mask.ravel()==1]
        inlierpts2 = pts2[mask.ravel()==1]

        # tracer les droites epipolaires
        imgL, imgR = drawFundamental(img1,img2,inlierpts1,inlierpts2,FRansac)

        #rendre les droites epipolaires paralleles
        imsize = (imgL.shape[1], imgL.shape[0])
        retval, H1, H2 = cv2.stereoRectifyUncalibrated(inlierpts1, inlierpts2, FRansac, imsize )

        #homographies H1, H2
        #rectImgL = cv2.warpPerspective(imgL, H1, imsize)
        #rectImgR = cv2.warpPerspective(imgR, H2, imsize)
        rectImgL = cv2.warpPerspective(img1, H1, imsize)
        rectImgR = cv2.warpPerspective(img2, H2, imsize)

        #vis = np.concatenate((rectImgL, rectImgR), axis=1)

        #cv2.imshow('epipolaires rectified',vis)
        #cv2.waitKey(1)

        #Need to Downsample !!!

        #Stereo BM
        win_size = 5 #5
        min_disp = -1
        max_disp = 63 #min_disp * 9
        #num_disp = max_disp - min_disp
        num_disp = 64

        stereo = cv2.StereoSGBM_create(minDisparity= min_disp,
            numDisparities = num_disp,
            blockSize = 11,
            uniquenessRatio = 10,
            speckleWindowSize = 5,
            speckleRange = 2,
            disp12MaxDiff = 1,
            P1 = 8*3*win_size**2,#8*3*win_size**2,
            P2 = 32*3*win_size**2) #32*3*win_size**2)

        #stereo = cv2.StereoBM_create(numDisparities=16, blockSize=5)

        print ("\nComputing the disparity  map...")
        disparity_map = stereo.compute(rectImgL, rectImgR)
        #disparity_map = stereo.compute(img1, img2)

        res = cv2.convertScaleAbs(disparity_map)

        cont_disparity_map = np.concatenate((img2, res), axis=1)
        
        #cv2.imshow('disparity_map', disparity_map)
        cv2.imshow('disparity_map', cont_disparity_map)
        cv2.waitKey(1)

if __name__ == '__main__':
    try:
        imageDepthMap = ImageDepthMap()
    except rospy.ROSInterruptException, KeyboardInterrupt: 
        pass
    rospy.spin()
    cv2.destroyAllWindows()
