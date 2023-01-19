cmake_minimum_required(VERSION 3.5)

# Find necessary packages
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclpy REQUIRED)
find_package(pcl_ros REQUIRED)
find_package(geodesy REQUIRED)
find_package(nmea_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(interactive_markers REQUIRED)
# find_package(eigen_conversions REQUIRED) # TODO: deal with it later, not sure if available in ROS2
find_package(pclomp REQUIRED) # instead of ndt_omp for ROS2
find_package(fast_gicp REQUIRED)

if (ament_cmake_FOUND)
    add_definitions(-DROS_AVAILABLE=2)
endif ()

ament_export_dependencies(rosidl_default_runtime)
ament_export_include_directories(include)
# ament_export_libraries(hdl_graph_slam_nodelet) # TODO insert this once ROS2 version is implemented

# the next line replaces add_message_files, add_service_files, generate_message in ROS2, TODO to be tested
set(msg_files
  "msg/EdgeEstimate.msg"
  "msg/EdgeRos.msg"
  "msg/FloorCoeffs.msg"
  "msg/GraphEstimate.msg"
  "msg/GraphRos.msg"
  "msg/KeyFrameEstimate.msg"
  "msg/KeyFrameRos.msg"
  "msg/PoseWithName.msg"
  "msg/PoseWithNameArray.msg"
  "msg/ScanMatchingStatus.msg"
  # in case one unified branch for ROS1 and ROS2 is wanted, use the following message definitions instead.
  # This is due to the fact that 'duration' and 'time' (ROS1) cannot easily be bridged to 'builin_interfaces/Time' 'builin_interfaces/Duration'
  # see # https://docs.ros.org/en/foxy/The-ROS2-Project/Contributing/Migration-Guide.html#message-service-and-action-definitions
  # "msg/KeyFrameRos2.msg"
  # "msg/KeyFrameEstimateRos2.msg"
  # "msg/GraphRos2.msg"
  # "msg/GraphEstimateRos2.msg"
)
  
set(srv_files
  "srv/DumpGraph.srv"
  "srv/GetGraphEstimate.srv"
  "srv/GetMap.srv"
  "srv/PublishGraph.srv"
  "srv/SaveMap.srv"
  # in case one unified branch for ROS1 and ROS2 is wanted, use the following message definitions instead
  # This is due to the fact that 'duration' and 'time' (ROS1) cannot easily be bridged to 'builin_interfaces/Time' 'builin_interfaces/Duration',
  # see # https://docs.ros.org/en/foxy/The-ROS2-Project/Contributing/Migration-Guide.html#message-service-and-action-definitions
  # "srv/GetGraphEstimateRos2.srv"
  # "srv/GetMapRos2.srv"
)

rosidl_generate_interfaces(${PROJECT_NAME}
    ${msg_files}
    ${srv_files}
    DEPENDENCIES std_msgs nmea_msgs sensor_msgs geometry_msgs 
)

# Finally create a pacakge
ament_package()