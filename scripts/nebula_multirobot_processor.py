import os
import time
import signal
import subprocess
from enum import Enum
import tqdm

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
import rclpy.logging
from tf2_ros import TransformBroadcaster
from rclpy.callback_groups import ReentrantCallbackGroup

import sqlite3
from rosidl_runtime_py.utilities import get_message
from rclpy.serialization import deserialize_message

from rosgraph_msgs.msg import Clock
from builtin_interfaces.msg import Time
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py.point_cloud2 import read_points
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped
from vamex_slam_msgs.msg import SlamStatus
from vamex_slam_msgs.srv import DumpGraph, SaveMap

import numpy as np
import math
import matplotlib.pyplot as plt
from scipy.spatial.transform import Rotation as R
# from pyquaternion import Quaternion


def euler_from_quaternion(x, y, z, w):
    """
    Convert a quaternion into euler angles (roll, pitch, yaw)
    roll is rotation around x in radians (counterclockwise)
    pitch is rotation around y in radians (counterclockwise)
    yaw is rotation around z in radians (counterclockwise)
    """
    t0 = +2.0 * (w * x + y * z)
    t1 = +1.0 - 2.0 * (x * x + y * y)
    roll_x = math.atan2(t0, t1)

    t2 = +2.0 * (w * y - z * x)
    t2 = +1.0 if t2 > +1.0 else t2
    t2 = -1.0 if t2 < -1.0 else t2
    pitch_y = math.asin(t2)

    t3 = +2.0 * (w * z + x * y)
    t4 = +1.0 - 2.0 * (y * y + z * z)
    yaw_z = math.atan2(t3, t4)

    return roll_x, pitch_y, yaw_z  # in radians


class Task(Enum):
    PLAYBACK = 0
    WAIT_SLAM_DONE = 1
    DUMP_GRAPH = 2
    SAVE_MAP = 3
    SHUTDOWN_SLAM = 4
    SHUTDOWN_NODE = 5


# https://answers.ros.org/question/358686/how-to-read-a-bag-file-in-ros2/
class BagFileParser():
    def __init__(self, bag_file):
        self.conn = sqlite3.connect(bag_file)
        self.cursor = self.conn.cursor()

        # create a message type map
        topics_data = self.cursor.execute("SELECT id, name, type FROM topics").fetchall()
        self.topic_type = {name_of: type_of for id_of, name_of, type_of in topics_data}
        self.topic_id = {name_of: id_of for id_of, name_of, type_of in topics_data}
        self.topic_msg_message = {name_of: get_message(type_of) for id_of, name_of, type_of in topics_data}

    def __del__(self):
        self.conn.close()

    # Return [(timestamp0, message0), (timestamp1, message1), ...]
    def get_messages(self, topic_name):

        topic_id = self.topic_id[topic_name]
        # Get from the db
        rows = self.cursor.execute("SELECT timestamp, data FROM messages WHERE topic_id = {}".format(topic_id)).fetchall()
        # Deserialise all and timestamp them
        return [(timestamp, deserialize_message(data, self.topic_msg_message[topic_name])) for timestamp, data in rows]


class NebulaProcessor(Node):
    def __init__(self) -> None:
        super().__init__('rosbag_processor')

        self.playback_rate = self.declare_parameter('rate', 1.0).get_parameter_value().double_value
        self.robot_names = self.declare_parameter('robot_names', ['husky1']).get_parameter_value().string_array_value
        self.dataset_base_dir = self.declare_parameter('dataset_base_dir', '/data/datasets/nebula').get_parameter_value().string_value
        self.dataset = self.declare_parameter('dataset', 'urban').get_parameter_value().string_value
        self.result_dir = self.declare_parameter('result_dir', '/data/Seafile/data/slam_results/nebula').get_parameter_value().string_value
        self.eval_name = self.declare_parameter('eval_name', 'urban_multi').get_parameter_value().string_value
        # -1.0 means use the resolution from the map, otherwise voxel size in meters
        self.map_resolution = self.declare_parameter('map_resolution', -1.0).get_parameter_value().double_value
        self.slam_config = self.declare_parameter('slam_config', 'nebula_multi_robot_urban.yaml').get_parameter_value().string_value
        self.enable_floor_detetction = self.declare_parameter('enable_floor_detetction', False).get_parameter_value().bool_value

        # The slam status callback and the timer callback need to be reentrant, so that the slam status can be updated while the timer is processed
        self.reentrant_callback_group = ReentrantCallbackGroup()

        self.tf_broadcaster = TransformBroadcaster(self)

        if self.dataset_base_dir == '':
            print('Please specify the dataset directory parameter <dataset_base_dir> like this: --ros-args -p dataset_base_dir:=/path/to/dataset')
            exit(1)

    def setup_playback(self):
        self.robots = {}  # type: dict[str, dict]
        print('Setting up playback for robots: {}'.format(self.robot_names))
        for robot_name in self.robot_names:
            self.setup_robot(robot_name)

        self.clock_publisher = self.create_publisher(Clock, '/clock', 10)
        print('Setting up Clock pubilsher on topic /clock')

    def setup_robot(self, robot_name):

        keyed_scan_bag_path = os.path.join(self.dataset_base_dir, self.dataset, 'rosbag', robot_name, robot_name + '.db3')
        odometry_bag_path = os.path.join(self.dataset_base_dir, self.dataset, 'ground_truth',
                                         robot_name + '_odom', robot_name + '_odom.db3')
        if not os.path.exists(keyed_scan_bag_path):
            print('Keyed scan bag does not exist: {}'.format(keyed_scan_bag_path))
            exit(1)
        if not os.path.exists(odometry_bag_path):
            print('Odometry bag does not exist: {}'.format(odometry_bag_path))
            exit(1)

        keyed_scans_parser = BagFileParser(keyed_scan_bag_path)
        keyed_scans_topic_name = '/' + robot_name + '/lamp/keyed_scans'
        print("Trying to get all messages from ros2 bag {} with topic name {}".format(keyed_scan_bag_path,  keyed_scans_topic_name))
        scans_msgs = keyed_scans_parser.get_messages(keyed_scans_topic_name)
        # Add the keyed scans data to the data dict
        self.robots[robot_name] = {}
        self.robots[robot_name]['scans_msgs'] = scans_msgs
        self.robots[robot_name]['scans_stamps'] = np.array([msg[0] for msg in scans_msgs])

        odometry_parser = BagFileParser(odometry_bag_path)
        odometry_topic_name = '/' + robot_name + '/lo_frontend/odometry'
        print("Trying to get all messages from ros2 bag {} with topic name {}".format(odometry_bag_path,  odometry_topic_name))
        odometry_msgs = odometry_parser.get_messages(odometry_topic_name)
        # odometry msg stamp is given in the header
        odometry_stamps = np.array([int(msg[1].header.stamp.sec * 1e9 + msg[1].header.stamp.nanosec) for msg in odometry_msgs])
        # Add the odometry to the data dict
        self.robots[robot_name]['odometry_msgs'] = odometry_msgs
        self.robots[robot_name]['odometry_stamps'] = odometry_stamps

        self.robots[robot_name]['scan_counter'] = 0
        point_cloud2_topic_name = '/' + robot_name + '/prefiltering/filtered_points'
        self.robots[robot_name]['point_cloud_pub'] = self.create_publisher(PointCloud2, point_cloud2_topic_name, 10)
        print('Setting up PointCloud2 publisher on topic {}'.format(point_cloud2_topic_name))
        odometry_topic_name = '/' + robot_name + '/scan_matching_odometry/odom'
        self.robots[robot_name]['odom_pub'] = self.create_publisher(Odometry,  odometry_topic_name, 10)
        print('Setting up Odometry publisher on topic {}'.format(odometry_topic_name))

        # Create the subscription to the slam status in order to stop playback when the algorithms are optimizing or loop closing
        slam_status_topic_name = '/' + robot_name + '/hdl_graph_slam/slam_status'
        self.robots[robot_name]['slam_status_subscription'] = self.create_subscription(
            SlamStatus, slam_status_topic_name, self.slam_status_callback, 10, callback_group=self.reentrant_callback_group)
        self.robots[robot_name]['slam_status'] = SlamStatus()

        # Setup a filtered_points publisher for floor detection
        if self.enable_floor_detetction:
            self.robots[robot_name]['filtered_points_publisher'] = self.create_publisher(
                PointCloud2, '/' + robot_name + '/prefiltering/filtered_points', 10)

        # setup the slam process and saving of data
        self.robots[robot_name]['slam_process'] = None  # type: subprocess.Popen
        self.robots[robot_name]['dump_graph_requested'] = False
        self.robots[robot_name]['dump_graph_done'] = False
        dump_graph_topic = '/' + robot_name + '/hdl_graph_slam/dump'
        self.robots[robot_name]['dump_service_client'] = self.create_client(
            DumpGraph, dump_graph_topic, callback_group=self.reentrant_callback_group)
        self.robots[robot_name]['save_map_requested'] = False
        self.robots[robot_name]['save_map_done'] = False
        save_map_topic = '/' + robot_name + '/hdl_graph_slam/save_map'
        self.robots[robot_name]['save_map_client'] = self.create_client(
            SaveMap, save_map_topic, callback_group=self.reentrant_callback_group)
        self.robots[robot_name]['result_dir'] = os.path.join(self.result_dir, self.dataset, self.eval_name, robot_name)
        if not os.path.exists(self.robots[robot_name]['result_dir']):
            os.makedirs(self.robots[robot_name]['result_dir'])
        else:
            self.get_logger().warn('Result directory {} already exists, overwriting'.format(self.robots[robot_name]['result_dir']))

        # Start the slam process with the correct starting position
        start_pos = self.robots[robot_name]['odometry_msgs'][0][1].pose.pose.position
        start_quat = self.robots[robot_name]['odometry_msgs'][0][1].pose.pose.orientation
        x, y, z = start_pos.x, start_pos.y, start_pos.z
        qx, qy, qz, qw = start_quat.x, start_quat.y, start_quat.z, start_quat.w
        rot = R.from_quat([qx, qy, qz, qw])
        yaw, pitch, roll = rot.as_euler('ZYX', degrees=False)
        print(f'Starting slam process for robot {robot_name} at position ({x}, {y}, {z}) \
                with orientation ({np.rad2deg( roll)}, {np.rad2deg(pitch)}, {np.rad2deg(yaw)})')
        slam_cmd = ['ros2', 'launch', 'hdl_graph_slam', 'hdl_multi_robot_graph_slam.launch.py', 'model_namespace:=' + robot_name,
                    'config:=' + self.slam_config, 'x:=' + str(x), 'y:=' + str(y), 'z:=' + str(z), 'yaw:=' + str(yaw), 'pitch:=' + str(pitch), 'roll:=' + str(roll)]
        with open(os.path.join(self.robots[robot_name]['result_dir'], 'slam.log'), 'w') as f:
            self.robots[robot_name]['slam_process'] = subprocess.Popen(slam_cmd, stdout=f, stderr=f)
            print(f'Started slam process for robot {robot_name} with pid {self.robots[robot_name]["slam_process"].pid} and cmd')
            print(' '.join(slam_cmd))
        time.sleep(3)

    def task_timer_callback(self):
        self.task_timer.cancel()
        if self.task == Task.PLAYBACK:
            self.playback()
        elif self.task == Task.WAIT_SLAM_DONE:
            self.wait_slam_done()
        elif self.task == Task.DUMP_GRAPH:
            self.dump_graph()
        elif self.task == Task.SAVE_MAP:
            self.save_map()
        elif self.task == Task.SHUTDOWN_SLAM:
            self.shutdown_slam()
        elif self.task == Task.SHUTDOWN_NODE:
            self.shutdown_node()
        else:
            print('Unknown task')
        self.task_timer.reset()

    def start_playback(self):
        self.setup_playback()

        print('Starting playback with rate {}'.format(self.playback_rate))
        self.task = Task.PLAYBACK
        # self.timer = self.create_timer(1.0 / self.playback_rate, self.playback, callback_group=self.reentrant_callback_group)
        self.task_timer = self.create_timer(1.0 / self.playback_rate, self.task_timer_callback,
                                            callback_group=self.reentrant_callback_group)
        self.print_wait_info_once = True
        total_scans = sum(len(self.robots[k]['scans_stamps']) for k in self.robots)
        self.progress_bar = tqdm.tqdm(total=total_scans, desc='Playback', unit='scans')

    def publish_transform(self, stamp, frame_id, child_frame_id,  translation, rotation):
        t = TransformStamped()
        t.header.stamp = stamp
        t.header.frame_id = frame_id
        t.child_frame_id = child_frame_id
        t.transform.translation.x = translation.x
        t.transform.translation.y = translation.y
        t.transform.translation.z = translation.z
        t.transform.rotation.x = rotation.x
        t.transform.rotation.y = rotation.y
        t.transform.rotation.z = rotation.z
        t.transform.rotation.w = rotation.w
        self.tf_broadcaster.sendTransform(t)

    def publish_clock_msg(self, stamp):
        clock_msg = Clock()
        clock_msg.clock.sec = stamp.sec
        clock_msg.clock.nanosec = stamp.nanosec
        self.clock_publisher.publish(clock_msg)

    def playback(self):
        if any(self.robots[robot_name]['slam_status'].in_optimization or
               self.robots[robot_name]['slam_status'].in_loop_closure or
               self.robots[robot_name]['slam_status'].in_graph_exchange for robot_name in self.robots):
            return
        # Make sure that this timer is only executed once, reset the timer at the end of this function
        # self.timer.cancel()
        # Get the robot name with the lowest timestamp

        robot_name = min(
            self.robots, key=lambda k: self.robots[k]['scans_stamps'][self.robots[k]['scan_counter']]
            if self.robots[k]['scan_counter'] < len(self.robots[k]['scans_stamps']) else float('inf'))

        # Get the pointcloud and the corresponding odometry message with the closest timestamp
        pointcloud_stamp = self.robots[robot_name]['scans_stamps'][self.robots[robot_name]['scan_counter']]
        closest_odometry_index = np.argmin(np.abs(self.robots[robot_name]['odometry_stamps'] - pointcloud_stamp))
        odometry_stamp = self.robots[robot_name]['odometry_stamps'][closest_odometry_index]

        pointcloud = self.robots[robot_name]['scans_msgs'][self.robots[robot_name]['scan_counter']][1].scan  # type: PointCloud2
        odometry = self.robots[robot_name]['odometry_msgs'][closest_odometry_index][1]
        # Publish the corresponding pointcloud and odometry message
        if pointcloud.header.frame_id == '':
            pointcloud.header.frame_id = robot_name + '/velodyne'
        odometry.child_frame_id = robot_name + '/base_link'

        # Set the header stamp of pointcloud message
        pointcloud.header.stamp.sec = int(str(pointcloud_stamp)[:len(str(pointcloud_stamp))-9])
        pointcloud.header.stamp.nanosec = int(str(pointcloud_stamp)[len(str(pointcloud_stamp))-9:])

        # Publish the tf2 transform between model_namespace/odom and model_namespace/base_link (model_namespace/velodyne) as both conincide
        # This is needed for the floor detection output visulization
        self.publish_transform(pointcloud.header.stamp, robot_name + '/odom', robot_name + '/base_link',
                               odometry.pose.pose.position, odometry.pose.pose.orientation)
        # t = TransformStamped()
        # t.header.stamp = pointcloud.header.stamp
        # t.header.frame_id = robot_name + '/odom'
        # t.child_frame_id = robot_name + '/base_link'
        # t.transform.translation.x = odometry.pose.pose.position.x
        # t.transform.translation.y = odometry.pose.pose.position.y
        # t.transform.translation.z = odometry.pose.pose.position.z
        # t.transform.rotation.x = odometry.pose.pose.orientation.x
        # t.transform.rotation.y = odometry.pose.pose.orientation.y
        # t.transform.rotation.z = odometry.pose.pose.orientation.z
        # t.transform.rotation.w = odometry.pose.pose.orientation.w
        # self.tf_broadcaster.sendTransform(t)

        while any(self.robots[k]['slam_status'].in_optimization or
                  self.robots[k]['slam_status'].in_loop_closure or
                  self.robots[k]['slam_status'].in_graph_exchange for k in self.robots):
            if self.print_wait_info_once:
                print('Waiting for slam to finish optimizing or loop closing')
            self.timer.reset()
            self.print_wait_info_once = False
            return
        self.print_wait_info_once = True

        if self.enable_floor_detetction:
            self.robots[robot_name]['filtered_points_publisher'].publish(pointcloud)
            # sleep for some time to give the floor detection node time to process the pointcloud
            time.sleep(0.3)

        print('{} scan #{}/{} stamp {:.3f} odom stamp {:.3f}: delta t {:.3f}s, publishing scan, odom'.format(
            robot_name, self.robots[robot_name]['scan_counter'], len(self.robots[robot_name]['scans_stamps']) - 1,
            pointcloud_stamp / 1e9, odometry_stamp / 1e9, (pointcloud_stamp - odometry_stamp) / 1e9))

        self.progress_bar.update(1)

        # Publish the matching pointcloud and odometry message
        self.robots[robot_name]['point_cloud_pub'].publish(pointcloud)
        self.robots[robot_name]['odom_pub'].publish(odometry)
        # Since we are not using a rosbag2 player, we need to publish the clock message ourselves
        # clock_msg = Clock()
        # clock_msg.clock.sec = pointcloud.header.stamp.sec
        # clock_msg.clock.nanosec = pointcloud.header.stamp.nanosec
        # self.clock_publisher.publish(clock_msg)
        self.publish_clock_msg(pointcloud.header.stamp)

        self.robots[robot_name]['scan_counter'] += 1

        # Reset the timer so we can proceed processing the next message
        # self.timer.reset()

        # Exit if all keyed scans have been processed
        if all(self.robots[k]['scan_counter'] == len(self.robots[k]['scans_stamps']) for k in self.robots):
            self.progress_bar.close()
            print('Finished playback, closing progress bar')
            # self.timer.destroy()
            self.task = Task.WAIT_SLAM_DONE
            # Trigger the optimization once more
            self.publish_clock_msg(Time(sec=pointcloud.header.stamp.sec + 5, nanosec=pointcloud.header.stamp.nanosec))
            # exit(0)

    def wait_slam_done(self):
        if any([self.robots[robot_name]['slam_status'].in_optimization for robot_name in self.robot_names]) or \
                any([self.robots[robot_name]['slam_status'].in_loop_closure for robot_name in self.robot_names]) or \
                any([self.robots[robot_name]['slam_status'].in_graph_exchange for robot_name in self.robot_names]):
            time.sleep(1)
            print('Slam is optimizing or in loop closure, waiting')
            return
        print('Slam is done, starting dump graph')
        self.task = Task.DUMP_GRAPH

    def slam_status_callback(self, msg):
        self.robots[msg.robot_name]['slam_status'] = msg

    def perform_async_service_call(self, client, request, robot_name):
        while client.wait_for_service(timeout_sec=1.0) is False:
            print('service', client.srv_name, 'not available, waiting again...')

        print('calling async service', client.srv_name)
        future = client.call_async(request)
        if isinstance(request, DumpGraph.Request):
            future.add_done_callback(self.get_done_dump_graph_callback(robot_name))
        if isinstance(request, SaveMap.Request):
            future.add_done_callback(self.get_done_save_map_callback(robot_name))

    def get_done_dump_graph_callback(self, robot_name):
        def done_dump_graph_callback(future):
            result = future.result()
            print(f'Dump graph service call for robot {robot_name} success? {result.success}')
            self.robots[robot_name]['dump_graph_done'] = True
            if all([self.robots[robot_name]['dump_graph_requested'] for robot_name in self.robot_names]):
                print('All dump graph requests done, starting save map')
                self.task = Task.SAVE_MAP
        return done_dump_graph_callback

    def get_done_save_map_callback(self, robot_name):
        def done_save_map_callback(future):
            result = future.result()
            print(f'Save map service call for robot {robot_name} success? {result.success}')
            self.robots[robot_name]['save_map_done'] = True
            if all([self.robots[robot_name]['save_map_requested'] for robot_name in self.robot_names]):
                self.task = Task.SHUTDOWN_SLAM
        return done_save_map_callback

    def dump_graph(self):
        robot_to_dump = None
        for robot_name in self.robot_names:
            if self.robots[robot_name]['dump_graph_requested']:
                if self.robots[robot_name]['dump_graph_done']:
                    continue
                else:
                    break
            else:
                robot_to_dump = robot_name
                break
        if robot_to_dump is None:
            return
        # call the dumb and save graph service on hdl graph slam
        dump_request = DumpGraph.Request()
        dump_request.destination = os.path.join(self.robots[robot_name]['result_dir'], 'g2o')
        self.robots[robot_name]['dump_graph_requested'] = True
        print(f'Dumping graph at: {dump_request.destination}')
        self.perform_async_service_call(self.robots[robot_name]['dump_service_client'], dump_request, robot_name)

    def save_map(self):
        robot_to_save = None
        for robot_name in self.robot_names:
            if self.robots[robot_name]['save_map_requested']:
                if self.robots[robot_name]['save_map_done']:
                    continue
                else:
                    break
            else:
                robot_to_save = robot_name
                break
        if robot_to_save is None:
            return
        save_map_request = SaveMap.Request()
        save_map_request.destination = os.path.join(self.robots[robot_name]['result_dir'], 'map.pcd')
        save_map_request.resolution = self.map_resolution
        self.robots[robot_name]['save_map_requested'] = True
        print(f'Saving map with resolution {save_map_request.resolution} at: {save_map_request.destination}')
        self.perform_async_service_call(self.robots[robot_name]['save_map_client'], save_map_request, robot_name)

    def shutdown_slam(self):
        for robot_name in self.robot_names:
            if self.robots[robot_name]['slam_process'] is not None:
                print(f'Sending SIGINT to slam process for robot {robot_name}')
                try:
                    self.robots[robot_name]['slam_process'].send_signal(subprocess.signal.SIGINT)
                    self.robots[robot_name]['slam_process'].wait(timeout=10)
                except subprocess.TimeoutExpired:
                    print(f'Timeout expired for robot {robot_name} slam process, trying SIGTERM')
                    self.robots[robot_name]['slam_process'].terminate()
                    self.robots[robot_name]['slam_process'].wait(timeout=10)
                finally:
                    print(f'Robot {robot_name} slam process terminated')

        self.task = Task.SHUTDOWN_NODE

    def shutdown_node(self):
        print('Shutting down node, rclpy, and sys')
        self.destroy_node()
        rclpy.shutdown()
        print('Shutting down system')
        exit()


def main(args=None):

    rclpy.init(args=args)
    # We need a MultiThreadedExecutor to process certain callbacks while within another callback
    executor = MultiThreadedExecutor()
    nebula_processor = NebulaProcessor()
    executor.add_node(nebula_processor)

    nebula_processor.start_playback()
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        rclpy.spin(node=nebula_processor, executor=executor)
    except KeyboardInterrupt:
        print('Trying to gracefully shutdown')
        nebula_processor.shutdown_slam()
        nebula_processor.shutdown_node()
    except Exception as e:
        print('Caught exception: {}'.format(e))
        nebula_processor.shutdown_slam()
        nebula_processor.shutdown_node()
    finally:
        print('Finally shutting down')
        nebula_processor.destroy_node()
        executor.shutdown()


if __name__ == '__main__':
    main()
