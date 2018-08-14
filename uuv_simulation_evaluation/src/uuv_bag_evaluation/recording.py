# Copyright (c) 2016 The UUV Simulator Authors.
# All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import rospy
import logging
import sys
import rosbag
import numpy as np
from uuv_trajectory_generator import TrajectoryGenerator, TrajectoryPoint


class Recording:
    __instance = None

    def __init__(self, filename):
        # Setting up the log
        self._logger = logging.getLogger('read_rosbag')
        if len(self._logger.handlers) == 0:
            out_hdlr = logging.StreamHandler(sys.stdout)
            out_hdlr.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(module)s | %(message)s'))
            out_hdlr.setLevel(logging.INFO)
            self._logger.addHandler(out_hdlr)
            self._logger.setLevel(logging.INFO)

        # Bag filename
        self._filename = filename
        self._bag = rosbag.Bag(filename)
        
        self._thruster_prefix = None
        self._fin_prefix = None
        self._topics = dict()

        for x in self._bag.get_type_and_topic_info():
            for k in x:                
                if 'error' in k and 'uuv_control_msgs/TrajectoryPoint' in x[k][0]:
                    self._topics['error'] = k
                    self._logger.info('Error topic found <%s>' % k)
                if 'reference' in k:
                    self._topics['trajectory'] = k
                    self._logger.info('Trajectory topic found <%s>' % k)
                if 'nav_msgs/Odometry' in x[k][0]:
                    self._topics['odometry'] = k
                    self._logger.info('Odometry topic found <%s>' % k)
                if 'current_velocity' in k:
                    self._topics['current_velocity'] = k
                    self._logger.info('Current velocity topic found <%s>' % k)
                if 'thruster_manager' in k and 'geometry_msgs/WrenchStamped' in x[k][0]:
                    self._topics['thruster_manager'] = k
                    self._logger.info('Thruster manager input topic found <%s>' % k)
                if 'wrench_perturbation' in k:
                    self._topics['wrench_perturbation'] = k
                    self._logger.info('Wrench perturbation topic found <%s>' % k)
                if self._thruster_prefix is None:
                    if 'thrusters' in k:
                        self._logger.info('Thruster output topic prefix found <%s>' % k)
                        i = len('thrusters')
                        i_max = k.find('thrusters') + i
                        self._thruster_prefix = k[:i_max]
                if self._fin_prefix is None:
                    if 'fins' in k:
                        self._logger.info('Fins output topic prefix found <%s>' % k)
                        i = len('fins')
                        i_max = k.find('fins') + i
                        self._fin_prefix = k[:i_max]

        self._is_init = False

        self._trajectories = dict(desired=None,
                                  actual=None)

        self._error = None

        self._thrusters = dict()
        self._thrusters_input = dict()
        self._thruster_manager_input = dict(time=list(), force=list(), torque=list())

        self._fins_input = dict()
        self._fins_output = dict()
        self._fins_wrench = dict()

        self._current_vel = dict(time=list(),
                                 vel=list())

        self._disturbance_wrench = dict(time=list(), force=list(), torque=list())

        self._read_data()

        if not self._is_init:
            self._logger.error('Error reading bag, filename=' + self._filename)

        Recording.__instance = self

    @classmethod
    def get_instance(cls):
        if cls.__instance is None:
            cls.__instance = Recording()
        return cls.__instance

    @property
    def is_init(self):
        return self._is_init

    @property
    def start_time(self):
        return self._trajectories['desired'].points[0].t

    @property
    def end_time(self):
        return self._trajectories['desired'].points[-1].t

    @property
    def desired(self):
        return self._trajectories['desired']

    @property
    def actual(self):
        return self._trajectories['actual']

    @property
    def errors(self):
        return self._error

    @property
    def n_thrusters(self):
        return len(self._thrusters.keys())

    @property
    def n_fins(self):
        return len(self._fin_wrench.keys())

    def _read_data(self):
        try:
            self._trajectories['desired'] = TrajectoryGenerator()
            for topic, msg, time in self._bag.read_messages(self._topics['trajectory']):
                self._trajectories['desired'].add_trajectory_point_from_msg(msg)

            self._trajectories['actual'] = TrajectoryGenerator()
        except Exception, e:
            self._logger.error('Error trajectories from rosbag, message=' + str(e))

        try:
            if 'odometry' in self._topics:
                for topic, msg, time in self._bag.read_messages(self._topics['odometry']):
                    t = msg.header.stamp.to_sec()

                    p = msg.pose.pose.position
                    q = msg.pose.pose.orientation
                    v = msg.twist.twist.linear
                    w = msg.twist.twist.angular

                    point = TrajectoryPoint(
                        t, np.array([p.x, p.y, p.z]),
                        np.array([q.x, q.y, q.z, q.w]),
                        np.array([v.x, v.y, v.z]),
                        np.array([w.x, w.y, w.z]),
                        np.array([0, 0, 0]),
                        np.array([0, 0, 0]))
                    # Store sampled trajectory point
                    self._trajectories['actual'].add_trajectory_point(point)
        except Exception, e:
            self._logger.error('Error retrieving odometry data from rosbag, message=' + str(e))

        try:
            if 'error' in self._topics:
                self._error = TrajectoryGenerator()
                for topic, msg, time in self._bag.read_messages(self._topics['error']):
                    self._error.add_trajectory_point_from_msg(msg)
                self._logger.info('Error topic loaded')
        except Exception, e:
            self._logger.error('Error retrieving error data from rosbag, message=' + str(e))

        try:
            # Find all thruster output topics
            if self._thruster_prefix is not None:
                for i in range(16):
                    for topic, msg, time in self._bag.read_messages('%s/%d/thrust' % (self._thruster_prefix, i)):
                        if i not in self._thrusters:
                            self._thrusters[i] = dict(time=list(), values=list())
                        t = msg.header.stamp.to_sec()
                        self._thrusters[i]['time'].append(t)
                        self._thrusters[i]['values'].append(float(msg.data))
        except Exception, e:
            self._logger.error('Error retrieving thrust output from rosbag, message=' + str(e))

        try:
            # Find all thruster input topics
            if self._thruster_prefix is not None:
                for i in range(16):
                    for topic, msg, time in self._bag.read_messages('%s/%d/input' % (self._thruster_prefix, i)):
                        if i not in self._thrusters_input:
                            self._thrusters_input[i] = dict(time=list(), values=list())
                        t = msg.header.stamp.to_sec()
                        self._thrusters_input[i]['time'].append(t)
                        self._thrusters_input[i]['values'].append(float(msg.data))
        except Exception, e:
            self._logger.error('Error retrieving thruster input data from rosbag, message=' + str(e))

        try:
            # Find all fins input topics
            if self._fin_prefix is not None:
                for i in range(16):
                    for topic, msg, time in self._bag.read_messages('%s/%d/input' % (self._fin_prefix, i)):
                        if i not in self._fins_input:
                            self._fins_input[i] = dict(time=list(), values=list())
                        t = msg.header.stamp.to_sec()
                        self._fins_input[i]['time'].append(t)
                        self._fins_input[i]['values'].append(float(msg.data))
        except Exception, e:
            self._logger.error('Error retrieving fin input data from rosbag, message=' + str(e))

        try:
            # Find all fins output topics
            if self._fin_prefix is not None:
                for i in range(16):
                    for topic, msg, time in self._bag.read_messages('%s/%d/output' % (self._fin_prefix, i)):
                        if i not in self._fins_output:
                            self._fins_output[i] = dict(time=list(), values=list())
                        t = msg.header.stamp.to_sec()
                        self._fins_output[i]['time'].append(t)
                        self._fins_output[i]['values'].append(float(msg.data))
        except Exception, e:
            self._logger.error('Error retrieving fin output data from rosbag, message=' + str(e))

        try:
            # Find all fin wrench topics
            if self._fin_prefix is not None:
                for i in range(16):
                    for topic, msg, time in self._bag.read_messages('%s/%d/wrench_topic' % (self._fin_prefix, i)):
                        if i not in self._fins_output:
                            self._fins_wrench[i] = dict(time=list(), force=list(), torque=list())
                        time = msg.header.stamp.to_sec()
                        self._fins_wrench[i]['time'].append(time)
                        self._fins_wrench[i]['force'].append([msg.wrench.force.x, msg.wrench.force.y, msg.wrench.force.z])
                        self._fins_wrench[i]['torque'].append([msg.wrench.torque.x, msg.wrench.torque.y, msg.wrench.torque.z])
        except Exception, e:
            self._logger.error('Error retrieving fin wrench data from rosbag, message=' + str(e))

        try:
            # Find wrench perturbation topic
            if 'wrench_perturbation' in self._topics:
                for topic, msg, time in self._bag.read_messages(self._topics['wrench_perturbation']):
                    time = msg.header.stamp.to_sec()
                    self._disturbance_wrench['time'].append(time)
                    self._disturbance_wrench['force'].append([msg.wrench.force.x, msg.wrench.force.y, msg.wrench.force.z])
                    self._disturbance_wrench['torque'].append([msg.wrench.torque.x, msg.wrench.torque.y, msg.wrench.torque.z])
        except Exception, e:
            self._logger.error('Error retrieving wrench perturbantion data from rosbag, message=' + str(e))

        try:
            # Find current velocity topic
            if 'current_velocity' in self._topics:
                for topic, msg, time in self._bag.read_messages(self._topics['current_velocity']):
                    time = msg.header.stamp.to_sec()
                    v = msg.twist.linear
                    self._current_vel['time'].append(time)
                    self._current_vel['vel'].append([v.x, v.y, v.z])
        except Exception, e:
            self._logger.error('Error retrieving current velocity data from rosbag, message=' + str(e))
        
        try:
            # Find thruster manager input topic
            if 'thruster_manager' in self._topics:
                for topic, msg, time in self._bag.read_messages(self._topics['thruster_manager']):
                    time = msg.header.stamp.to_sec()
                    self._thruster_manager_input['time'].append(time)
                    self._thruster_manager_input['force'].append([msg.wrench.force.x, msg.wrench.force.y, msg.wrench.force.z])
                    self._thruster_manager_input['torque'].append([msg.wrench.torque.x, msg.wrench.torque.y, msg.wrench.torque.z])            
        except Exception, e:
            self._logger.error('Error retrieving thruster manager input wrench data from rosbag, message=' + str(e))

        self._is_init = True

    def get_time(self):
        if not self._is_init:
            return list()
        return self._trajectories['actual'].times

    def get_thruster_data(self, index):
        if not self._is_init:
            print self.__class__.__name__, '- Thruster data has not been parsed'
            return None, None
        if index not in self._thrusters:
            print self.__class__.__name__, '- Invalid thruster index, value=', index
            return None, None
        return self._thrusters[index]['time'], self._thrusters[index]['values']

    def get_trajectory_coord(self, tag):
        assert tag in self._trajectories, 'Invalid trajectory tag'
        x = [p.p[0] for p in self._trajectories[tag].points]
        y = [p.p[1] for p in self._trajectories[tag].points]
        z = [p.p[2] for p in self._trajectories[tag].points]
        return x, y, z

    def get_thruster_data(self, idx):
        assert idx in self._thrusters, 'Invalid thruster index, idx=' + str(idx)
        return self._thrusters[idx]['time'], self._thrusters[idx]['values']

    def get_thruster_input_data(self, idx):
        assert idx in self._thrusters_input, 'Invalid thruster index, idx=' + str(idx)
        return self._thrusters_input[idx]['time'], self._thrusters_input[idx]['values']

    def get_thruster_manager_input(self):
        return self._thruster_manager_input['time'], self._thruster_manager_input['force'], self._thruster_manager_input['torque']

    def get_fin_input_data(self, idx):
        assert idx in self._fins_input, 'Invalid fin index, idx=' + str(idx)
        return self._fins_input[idx]['time'], self._fins_input[idx]['values']

    def get_fin_output_data(self, idx):
        assert idx in self._fins_output, 'Invalid fin index, idx=' + str(idx)
        return self._fins_output[idx]['time'], self._fins_output[idx]['values']

    def get_fin_wrench_data(self, idx):
        assert idx in self._fins_wrench, 'Invalid fin index, idx=' + str(idx)
        return self._fins_wrench[idx]['time'], self._fins_wrench[idx]['force'], self._fins_wrench[idx]['torque']

    def get_current_vel(self):
        return self._current_vel['time'], self._current_vel['vel']

    def get_wrench_dist(self):
        return self._disturbance_wrench['time'], self._disturbance_wrench['force'], self._disturbance_wrench['torque']
