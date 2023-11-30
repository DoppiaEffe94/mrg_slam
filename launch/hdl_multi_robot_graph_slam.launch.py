import os
import yaml

from ament_index_python import get_package_share_directory

from launch import LaunchDescription
from launch.actions import OpaqueFunction, DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.actions import LoadComposableNodes
from launch_ros.descriptions import ComposableNode
import numpy as np

# Parameter type mapping to infer the correct data type from the cli string
PARAM_MAPPING = {
    'model_namespace': str,
    'use_sim_time': bool,
    'start_rviz2': bool,
    'enable_floor_detection': bool,
    'enable_gps': bool,
    'enable_imu_acceleration': bool,
    'enable_imu_orientation': bool,
    'tf_link_values': str,
    'points_topic': str,
    'map_frame_id': str,
    'odom_frame_id': str,
    'robot_odom_frame_id': str,
    'enable_robot_odometry_init_guess': bool,
    'imu_topic': str,
    'x': float,
    'y': float,
    'z': float,
    'roll': float,
    'pitch': float,
    'yaw': float,
    'init_pose_topic': str,
}


# Overwrite the parameters from the yaml file with the ones from the cli if the cli string is not empty
def overwrite_yaml_params_from_cli(yaml_params, cli_params):
    for key, value in cli_params.items():
        if key in yaml_params and value != '':
            # Since all parameters from cli in ROS2 are strings, we need to infer the correct data type
            yaml_params[key] = PARAM_MAPPING[key](value)
            # Overwrite the boolean values since they are not correctly parsed, non empty strings are always True
            if value == 'true' or value == 'True':
                yaml_params[key] = True
            elif value == 'false' or value == 'False':
                yaml_params[key] = False
    return yaml_params


# Print unsorted yaml parameters
def print_yaml_params(yaml_params, header=None):
    if header is not None:
        print('######## ' + header + ' ########')
    print(yaml.dump(yaml_params, sort_keys=False, default_flow_style=False))


def print_remappings(remappings, header=None):
    if header is not None:
        print('######## ' + header + ' remappings ########')
    [print(remapping[0] + ' -> ' + remapping[1]) for remapping in remappings]
    print('')


def launch_setup(context, *args, **kwargs):

    config_file = 'hdl_multi_robot_graph_slam.yaml'
    if 'config' in context.launch_configurations:
        config_file = context.launch_configurations['config']
    config_file_path = os.path.join(
        get_package_share_directory('hdl_graph_slam'),
        'config',
        config_file
    )

    with open(config_file_path, 'r') as file:
        config_params = yaml.safe_load(file)
        print('Loaded config file: ' + config_file_path)
        # # Print all parameters from the yaml file for convenience when launching the nodes
        # print(yaml.dump(config_params, sort_keys=False, default_flow_style=False))
        shared_params = config_params['/**']['ros__parameters']
        static_transform_params = config_params['lidar2base_publisher']['ros__parameters']
        map2robotmap_publisher_params = config_params['map2robotmap_publisher']['ros__parameters']
        clock_publisher_ros2_params = config_params['clock_publisher_ros2']['ros__parameters']
        prefiltering_params = config_params['prefiltering_component']['ros__parameters']
        scan_matching_odometry_params = config_params['scan_matching_odometry_component']['ros__parameters']
        floor_detection_params = config_params['floor_detection_component']['ros__parameters']
        hdl_graph_slam_params = config_params['hdl_graph_slam_component']['ros__parameters']

    # Overwrite the parameters from the yaml file with the ones from the cli
    shared_params = overwrite_yaml_params_from_cli(shared_params, context.launch_configurations)
    static_transform_params = overwrite_yaml_params_from_cli(static_transform_params, context.launch_configurations)
    map2robotmap_publisher_params = overwrite_yaml_params_from_cli(map2robotmap_publisher_params, context.launch_configurations)
    prefiltering_params = overwrite_yaml_params_from_cli(prefiltering_params, context.launch_configurations)
    scan_matching_odometry_params = overwrite_yaml_params_from_cli(scan_matching_odometry_params, context.launch_configurations)
    floor_detection_params = overwrite_yaml_params_from_cli(floor_detection_params, context.launch_configurations)
    hdl_graph_slam_params = overwrite_yaml_params_from_cli(hdl_graph_slam_params, context.launch_configurations)

    model_namespace = shared_params['model_namespace']

    print_yaml_params(shared_params, 'shared_params')
    print_yaml_params(static_transform_params, 'static_transform_params')
    print_yaml_params(map2robotmap_publisher_params, 'map2robotmap_publisher_params')
    print_yaml_params(clock_publisher_ros2_params, 'clock_publisher_ros2_params')
    print_yaml_params(prefiltering_params, 'prefiltering_params')
    print_yaml_params(scan_matching_odometry_params, 'scan_matching_odometry_params')
    print_yaml_params(floor_detection_params, 'floor_detection_params')
    print_yaml_params(hdl_graph_slam_params, 'hdl_graph_slam_params')

    # Create the static transform publisher node
    frame_id = model_namespace + '/' + static_transform_params['base_frame_id']
    child_frame_id = model_namespace + '/' + static_transform_params['lidar_frame_id']
    static_transform_publisher = Node(
        # name=model_namespace + '_lidar2base_publisher',
        name='lidar2base_publisher',
        namespace=model_namespace,
        package='tf2_ros',
        executable='static_transform_publisher',
        # arguments has to be a list of strings
        arguments=[str(static_transform_params['lidar2base_x']),
                   str(static_transform_params['lidar2base_y']),
                   str(static_transform_params['lidar2base_z']),
                   str(static_transform_params['lidar2base_roll']),
                   str(static_transform_params['lidar2base_pitch']),
                   str(static_transform_params['lidar2base_yaw']),
                   frame_id,
                   child_frame_id],
        parameters=[shared_params],
        output='both'
    )

    # Create the map2robotmap publisher node
    if map2robotmap_publisher_params['enable_map2robotmap_publisher']:
        map2robotmap_child_frame_id = model_namespace + '/' + map2robotmap_publisher_params['map2robotmap_child_frame_id']
        map2robotmap_publisher = Node(
            name='map2robotmap_publisher',
            namespace=model_namespace,
            package='tf2_ros',
            executable='static_transform_publisher',
            # arguments has to be a list of strings
            arguments=[str(map2robotmap_publisher_params['map2robotmap_x']),
                       str(map2robotmap_publisher_params['map2robotmap_y']),
                       str(map2robotmap_publisher_params['map2robotmap_z']),
                       str(map2robotmap_publisher_params['map2robotmap_roll']),
                       str(map2robotmap_publisher_params['map2robotmap_pitch']),
                       str(map2robotmap_publisher_params['map2robotmap_yaw']),
                       map2robotmap_publisher_params['map2robotmap_frame_id'],
                       map2robotmap_child_frame_id],
            parameters=[shared_params],
            output='both'
        )

    # Start rviz2 from this launch file if set to true
    if shared_params['start_rviz2']:
        rviz2 = Node(
            name='rviz2',
            package='rviz2',
            executable='rviz2',
            arguments=['-d', os.path.join(get_package_share_directory(
                'hdl_graph_slam'), 'rviz', 'hdl_multi_robot_graph_slam_ros2.rviz')],
            parameters=[shared_params],
            output='both'
        )

    # In case we play a rosbag in ROS2 foxy, we need to publish the clock from the rosbag to the /clock topic
    if os.path.expandvars('$ROS_DISTRO') != 'humble':
        clock_publisher_ros2 = Node(
            package='hdl_graph_slam',
            executable='clock_publisher_ros2.py',
            name=model_namespace + '_clock_publisher_ros2',
            parameters=[clock_publisher_ros2_params, shared_params],
            output='both'
        )

    # Create the map2odom publisher node
    remaps = [('/hdl_graph_slam/odom2pub', '/' + model_namespace + '/hdl_graph_slam/odom2pub')]
    print_remappings(remaps, 'map2odom_publisher_ros2')
    map2odom_publisher_ros2 = Node(
        package='hdl_graph_slam',
        executable='map2odom_publisher_ros2.py',
        name='map2odom_publisher_ros2',
        namespace=model_namespace,
        output='both',
        parameters=[hdl_graph_slam_params, shared_params],
        remappings=remaps
    )

    # Create the container node
    # container_name = model_namespace + '_hdl_graph_slam_container'
    # If the launch command provides the debug argument we add the prefix to start gdbserver (move this to the node you need to debug)
    # More information can be found in hdl_multi_robot_graph_slam_debug.launch.py and at https://gist.github.com/JADC362/a4425c2d05cdaadaaa71b697b674425f
    if 'debug' in context.launch_configurations:
        prefix = ['gdbserver localhost:3000']
    else:
        prefix = []
    container_name = model_namespace + '/hdl_graph_slam_container'  # used in composable nodes
    container = Node(
        package='rclcpp_components',
        executable='component_container_mt',
        name="hdl_graph_slam_container",
        namespace=model_namespace,
        output='both',
        parameters=[shared_params],
        prefix=prefix
    )

    # Create the composable nodes, change names, topics, remappings to avoid conflicts for the multi robot case
    prefiltering_params['base_link_frame'] = model_namespace + '/' + prefiltering_params['base_link_frame']
    remaps = [('/imu/data', '/' + model_namespace + shared_params['imu_topic']),
              ('/velodyne_points', '/' + model_namespace + shared_params['points_topic']),
              ('/prefiltering/filtered_points', '/' + model_namespace + '/prefiltering/filtered_points'),
              ('/prefiltering/colored_points', '/' + model_namespace + '/prefiltering/colored_points')]
    print_remappings(remaps, 'prefiltering_component')
    if prefiltering_params['enable_prefiltering']:
        prefiltering_node = ComposableNode(
            package='hdl_graph_slam',
            plugin='hdl_graph_slam::PrefilteringComponent',
            name='prefiltering_component',
            namespace=model_namespace,
            parameters=[prefiltering_params, shared_params],
            remappings=remaps,
            extra_arguments=[{'use_intra_process_comms': True}]
        )

    remaps = [('/points_topic', '/' + model_namespace + shared_params['points_topic']),
              ('/filtered_points', '/' + model_namespace + '/prefiltering/filtered_points'),
              ('/scan_matching_odometry/transform', '/' + model_namespace + '/scan_matching_odometry/transform'),
              ('/scan_matching_odometry/read_until', '/' + model_namespace + '/scan_matching_odometry/read_until'),
              ('/scan_matching_odometry/status', '/' + model_namespace + '/scan_matching_odometry/status'),
              ('/scan_matching_odometry/odom', '/' + model_namespace + '/scan_matching_odometry/odom'),
              ('/scan_matching_odometry/aligned_points', '/' + model_namespace + '/scan_matching_odometry/aligned_points'),]
    print_remappings(remaps, 'scan_matching_odometry_component')
    # set the correct frame ids according to the model namespace
    scan_matching_odometry_params['odom_frame_id'] = model_namespace + '/' + scan_matching_odometry_params['odom_frame_id']
    scan_matching_odometry_params['robot_odom_frame_id'] = model_namespace + '/' + scan_matching_odometry_params['robot_odom_frame_id']
    if scan_matching_odometry_params['enable_scan_matching_odometry']:
        scan_matching_odometry_node = ComposableNode(
            package='hdl_graph_slam',
            plugin='hdl_graph_slam::ScanMatchingOdometryComponent',
            name='scan_matching_odometry_component',
            namespace=model_namespace,
            parameters=[scan_matching_odometry_params, shared_params],
            remappings=remaps,
            extra_arguments=[{'use_intra_process_comms': True}]
        )

    if scan_matching_odometry_params['enable_scan_matching_odometry'] and scan_matching_odometry_params['enable_odom_to_file']:
        remaps = [('/odom', '/' + model_namespace + '/scan_matching_odometry/odom')]
        odom_to_file_node = Node(
            name='scan_matching_odom_to_file',
            package='vamex_sim_rovers',
            executable='odom_to_file.py',
            namespace=model_namespace,
            remappings=remaps,
            output='screen',
            parameters=[{'result_file': '/tmp/' + model_namespace + '_scan_matching_odom.txt',
                         'every_n': 1}],
        )

    remaps = [('/points_topic', '/' + model_namespace + shared_params['points_topic']),
              ('/filtered_points', '/' + model_namespace + '/prefiltering/filtered_points'),
              ('/floor_detection/floor_coeffs', '/' + model_namespace + '/floor_detection/floor_coeffs'),
              ('/floor_detection/floor_filtered_points', '/' + model_namespace + '/floor_detection/floor_filtered_points'),
              ('/floor_detection/read_until', '/' + model_namespace + '/floor_detection/read_until'),
              ('/floor_detection/floor_points', '/' + model_namespace + '/floor_detection/floor_points')]
    print_remappings(remaps, 'floor_detection_component')
    if floor_detection_params['enable_floor_detection']:
        floor_detection_node = ComposableNode(
            package='hdl_graph_slam',
            plugin='hdl_graph_slam::FloorDetectionComponent',
            name='floor_detection_component',
            namespace=model_namespace,
            parameters=[floor_detection_params, shared_params],
            remappings=remaps,
            extra_arguments=[{'use_intra_process_comms': True}]
        )

    if hdl_graph_slam_params['enable_graph_slam']:
        # TODO remove own_name from hdl_graph_slam_params.yaml and use the model_namespace instead
        hdl_graph_slam_params['own_name'] = model_namespace
        # Overwrite init_pose array with the actual values
        hdl_graph_slam_params['init_pose'][0] = hdl_graph_slam_params['x']
        hdl_graph_slam_params['init_pose'][1] = hdl_graph_slam_params['y']
        hdl_graph_slam_params['init_pose'][2] = hdl_graph_slam_params['z']
        hdl_graph_slam_params['init_pose'][3] = np.deg2rad(hdl_graph_slam_params['yaw'])
        hdl_graph_slam_params['init_pose'][4] = np.deg2rad(hdl_graph_slam_params['pitch'])
        hdl_graph_slam_params['init_pose'][5] = np.deg2rad(hdl_graph_slam_params['roll'])
        # set the correct frame ids according to the model namespace
        hdl_graph_slam_params['map_frame_id'] = model_namespace + '/' + hdl_graph_slam_params['map_frame_id']
        hdl_graph_slam_params['odom_frame_id'] = model_namespace + '/' + hdl_graph_slam_params['odom_frame_id']
        remaps = [('/imu/data', shared_params['imu_topic']),
                  ('/filtered_points', '/' + model_namespace + '/prefiltering/filtered_points'),
                  ('/odom', '/' + model_namespace + '/scan_matching_odometry/odom'),
                  ('/floor_coeffs', '/' + model_namespace + '/floor_detection/floor_coeffs'),
                  ('/hdl_graph_slam/map_points', '/' + model_namespace + '/hdl_graph_slam/map_points'),
                  ('/hdl_graph_slam/markers', '/' + model_namespace + '/hdl_graph_slam/markers'),
                  ('/hdl_graph_slam/markers_node_names', '/' + model_namespace + '/hdl_graph_slam/markers_node_names'),
                  ('/hdl_graph_slam/markers_covariance', '/' + model_namespace + '/hdl_graph_slam/markers_covariance'),
                  ('/hdl_graph_slam/odom2pub', '/' + model_namespace + '/hdl_graph_slam/odom2pub'),
                  ('/hdl_graph_slam/read_until', '/' + model_namespace + '/hdl_graph_slam/read_until'),
                  ('/hdl_graph_slam/others_poses', '/' + model_namespace + '/hdl_graph_slam/others_poses'),
                  ('/hdl_graph_slam/publish_graph', '/' + model_namespace + '/hdl_graph_slam/publish_graph'),
                  ('/hdl_graph_slam/slam_status', '/' + model_namespace + '/hdl_graph_slam/slam_status'),
                  ('/hdl_graph_slam/dump', '/' + model_namespace + '/hdl_graph_slam/dump'),
                  ('/hdl_graph_slam/save_map', '/' + model_namespace + '/hdl_graph_slam/save_map'),
                  ('/hdl_graph_slam/get_map', '/' + model_namespace + '/hdl_graph_slam/get_map'),
                  ('/hdl_graph_slam/get_graph_estimate', '/' + model_namespace + '/hdl_graph_slam/get_graph_estimate'),
                  ('/hdl_graph_slam/request_graph', '/' + model_namespace + '/hdl_graph_slam/request_graph'),
                  ('/hdl_graph_slam/save_gids', '/' + model_namespace + '/hdl_graph_slam/save_gids'),
                  ('/hdl_graph_slam/get_graph_gids', '/' + model_namespace + '/hdl_graph_slam/get_graph_gids'),]
        print_remappings(remaps, 'hdl_graph_slam_component')
        hdl_graph_slam_node = ComposableNode(
            package='hdl_graph_slam',
            plugin='hdl_graph_slam::HdlGraphSlamComponent',
            name='hdl_graph_slam_component',
            namespace=model_namespace,
            parameters=[hdl_graph_slam_params, shared_params],
            remappings=remaps,
            extra_arguments=[{'use_intra_process_comms': True}]
        )

    composable_nodes = []
    if prefiltering_params['enable_prefiltering']:
        composable_nodes.append(prefiltering_node)
    if scan_matching_odometry_params['enable_scan_matching_odometry']:
        composable_nodes.append(scan_matching_odometry_node)
    if floor_detection_params['enable_floor_detection']:
        composable_nodes.append(floor_detection_node)
    if hdl_graph_slam_params['enable_graph_slam']:
        composable_nodes.append(hdl_graph_slam_node)

    # https://docs.ros.org/en/foxy/How-To-Guides/Launching-composable-nodes.html
    load_composable_nodes = LoadComposableNodes(
        target_container=container_name,
        composable_node_descriptions=composable_nodes
    )

    launch_description_list = [static_transform_publisher]
    if map2robotmap_publisher_params['enable_map2robotmap_publisher']:
        launch_description_list.append(map2robotmap_publisher)
    if shared_params['start_rviz2']:
        launch_description_list.append(rviz2)
    # For ROS2 foxy we need to add our own clock publisher, from ROS2 humble we can publish the clock topic with ros2 bag play <bag> --clock
    if os.path.expandvars('$ROS_DISTRO') != 'humble':
        launch_description_list.append(clock_publisher_ros2)
    launch_description_list.append(map2odom_publisher_ros2)
    launch_description_list.append(container)
    launch_description_list.append(load_composable_nodes)
    if scan_matching_odometry_params['enable_scan_matching_odometry'] and scan_matching_odometry_params['enable_odom_to_file']:
        launch_description_list.append(odom_to_file_node)

    # Return nodes
    return launch_description_list


def generate_launch_description():
    launch_description_list = []
    for param_name, _ in PARAM_MAPPING.items():
        launch_description_list.append(DeclareLaunchArgument(name=param_name, default_value=''))
    launch_description_list.append(OpaqueFunction(function=launch_setup))

    return LaunchDescription(launch_description_list)
