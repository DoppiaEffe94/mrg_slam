// SPDX-License-Identifier: BSD-2-Clause

#include <pcl/filters/approximate_voxel_grid.h>
#include <pcl/filters/passthrough.h>
#include <pcl/filters/radius_outlier_removal.h>
#include <pcl/filters/statistical_outlier_removal.h>
#include <pcl/filters/voxel_grid.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <pcl_conversions/pcl_conversions.h>
#include <tf2/exceptions.h>
#include <tf2_ros/buffer.h>
#include <tf2_ros/transform_listener.h>

#include <geometry_msgs/msg/transform_stamped.hpp>
#include <mrg_slam/ros_utils.hpp>
#include <pcl_ros/transforms.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>
#include <string>

namespace mrg_slam {

class PrefilteringComponent : public rclcpp::Node {
public:
    typedef pcl::PointXYZI PointT;

    // We need to pass NodeOptions in ROS2 to register a component
    PrefilteringComponent( const rclcpp::NodeOptions& options ) : Node( "prefiltering_component", options )
    {
        RCLCPP_INFO( this->get_logger(), "Initializing prefiltering_component..." );

        initialize_params();

        if( downsample_method == "VOXELGRID" ) {
            std::cout << "downsample: VOXELGRID " << downsample_resolution << std::endl;
            auto voxelgrid = new pcl::VoxelGrid<PointT>();
            voxelgrid->setLeafSize( downsample_resolution, downsample_resolution, downsample_resolution );
            downsample_filter.reset( voxelgrid );
        } else if( downsample_method == "APPROX_VOXELGRID" ) {
            std::cout << "downsample: APPROX_VOXELGRID " << downsample_resolution << std::endl;
            pcl::ApproximateVoxelGrid<PointT>::Ptr approx_voxelgrid( new pcl::ApproximateVoxelGrid<PointT>() );
            approx_voxelgrid->setLeafSize( downsample_resolution, downsample_resolution, downsample_resolution );
            downsample_filter = approx_voxelgrid;
        } else {
            if( downsample_method != "NONE" ) {
                std::cerr << "warning: unknown downsampling type (" << downsample_method << ")" << std::endl;
                std::cerr << "       : use passthrough filter" << std::endl;
            }
            std::cout << "downsample: NONE" << std::endl;
        }

        if( outlier_removal_method == "STATISTICAL" ) {
            std::cout << "outlier_removal: STATISTICAL " << statistical_mean_k << " - " << statistical_stddev_mul_thresh << std::endl;
            pcl::StatisticalOutlierRemoval<PointT>::Ptr sor( new pcl::StatisticalOutlierRemoval<PointT>() );
            sor->setMeanK( statistical_mean_k );
            sor->setStddevMulThresh( statistical_stddev_mul_thresh );
            outlier_removal_filter = sor;
        } else if( outlier_removal_method == "RADIUS" ) {
            std::cout << "outlier_removal: RADIUS " << radius_radius << " - " << radius_min_neighbors << std::endl;

            pcl::RadiusOutlierRemoval<PointT>::Ptr rad( new pcl::RadiusOutlierRemoval<PointT>() );
            rad->setRadiusSearch( radius_radius );
            rad->setMinNeighborsInRadius( radius_min_neighbors );
            outlier_removal_filter = rad;
        } else {
            std::cout << "outlier_removal: NONE" << std::endl;
        }

        if( use_deskewing ) {
            imu_sub = this->create_subscription<sensor_msgs::msg::Imu>( "imu/data", rclcpp::QoS( 1 ),
                                                                        std::bind( &PrefilteringComponent::imu_callback, this,
                                                                                   std::placeholders::_1 ) );
        }

        points_sub  = this->create_subscription<sensor_msgs::msg::PointCloud2>( "velodyne_points", rclcpp::QoS( 64 ),
                                                                                std::bind( &PrefilteringComponent::cloud_callback, this,
                                                                                           std::placeholders::_1 ) );
        points_pub  = this->create_publisher<sensor_msgs::msg::PointCloud2>( "prefiltering/filtered_points", rclcpp::QoS( 32 ) );
        colored_pub = this->create_publisher<sensor_msgs::msg::PointCloud2>( "prefiltering/colored_points", rclcpp::QoS( 32 ) );

        // setup transform listener
        tf_buffer   = std::make_unique<tf2_ros::Buffer>( this->get_clock() );
        tf_listener = std::make_shared<tf2_ros::TransformListener>( *tf_buffer );

        // Optionally print the all parameters declared in this node so far
        print_ros2_parameters( this->get_node_parameters_interface(), this->get_logger() );
    }

    virtual ~PrefilteringComponent() {}

private:
    void initialize_params()
    {
        // Declare and set parameters
        downsample_method     = this->declare_parameter<std::string>( "downsample_method", "VOXELGRID" );
        downsample_resolution = this->declare_parameter<double>( "downsample_resolution", 0.1 );

        outlier_removal_method        = this->declare_parameter<std::string>( "outlier_removal_method", "STATISTICAL" );
        statistical_mean_k            = this->declare_parameter<int>( "statistical_mean_k", 20 );
        statistical_stddev_mul_thresh = this->declare_parameter<double>( "statistical_stddev", 1.0 );
        radius_radius                 = this->declare_parameter<double>( "radius_radius", 0.8 );
        radius_min_neighbors          = this->declare_parameter<int>( "radius_min_neighbors", 2 );

        use_distance_filter  = this->declare_parameter<bool>( "use_distance_filter", true );
        distance_near_thresh = this->declare_parameter<double>( "distance_near_thresh", 1.0 );
        distance_far_thresh  = this->declare_parameter<double>( "distance_far_thresh", 100.0 );

        scan_period     = this->declare_parameter<double>( "scan_period", 0.1 );
        use_deskewing   = this->declare_parameter<bool>( "deskewing", false );
        base_link_frame = this->declare_parameter<std::string>( "base_link_frame", "base_link" );
    }

    void imu_callback( sensor_msgs::msg::Imu::ConstSharedPtr imu_msg ) { imu_queue.push_back( imu_msg ); }

    void cloud_callback( sensor_msgs::msg::PointCloud2::ConstSharedPtr src_cloud_ros )
    {
        // Convert to pcl pointcloud from ros PointCloud2
        pcl::PointCloud<PointT>::Ptr src_cloud = std::make_shared<pcl::PointCloud<PointT>>();
        pcl::fromROSMsg( *src_cloud_ros, *src_cloud );
        if( src_cloud->empty() ) {
            return;
        }

        src_cloud = deskewing( src_cloud );

        // if base_link_frame is defined, transform the input cloud to the frame
        if( !base_link_frame.empty() ) {
            geometry_msgs::msg::TransformStamped transform;
            try {
                // lookupTransform contains a Duration as parameter
                transform = tf_buffer->lookupTransform( base_link_frame, src_cloud->header.frame_id, rclcpp::Time( 0 ),
                                                        rclcpp::Duration( 2, 0 ) );
            } catch( const tf2::TransformException& ex ) {
                RCLCPP_WARN( this->get_logger(), "Could not transform source frame %s to target frame %s: %s",
                             src_cloud->header.frame_id.c_str(), base_link_frame.c_str(), ex.what() );
                RCLCPP_WARN( this->get_logger(), "Returning early in cloud_callback from prefiltering component" );
                return;
            }

            pcl::PointCloud<PointT>::Ptr transformed( new pcl::PointCloud<PointT>() );
            pcl_ros::transformPointCloud( *src_cloud, *transformed, transform );
            transformed->header.frame_id = base_link_frame;
            transformed->header.stamp    = src_cloud->header.stamp;
            src_cloud                    = transformed;
        }

        pcl::PointCloud<PointT>::ConstPtr filtered = distance_filter( src_cloud );
        filtered                                   = downsample( filtered );
        filtered                                   = outlier_removal( filtered );
        sensor_msgs::msg::PointCloud2 filtered_ros;
        pcl::toROSMsg( *filtered, filtered_ros );

        points_pub->publish( filtered_ros );
    }

    pcl::PointCloud<PointT>::ConstPtr downsample( const pcl::PointCloud<PointT>::ConstPtr& cloud ) const
    {
        if( !downsample_filter ) {
            return cloud;
        }

        pcl::PointCloud<PointT>::Ptr filtered( new pcl::PointCloud<PointT>() );
        downsample_filter->setInputCloud( cloud );
        downsample_filter->filter( *filtered );
        filtered->header = cloud->header;

        return filtered;
    }

    pcl::PointCloud<PointT>::ConstPtr outlier_removal( const pcl::PointCloud<PointT>::ConstPtr& cloud ) const
    {
        if( !outlier_removal_filter ) {
            return cloud;
        }

        pcl::PointCloud<PointT>::Ptr filtered( new pcl::PointCloud<PointT>() );
        outlier_removal_filter->setInputCloud( cloud );
        outlier_removal_filter->filter( *filtered );
        filtered->header = cloud->header;

        return filtered;
    }

    pcl::PointCloud<PointT>::ConstPtr distance_filter( const pcl::PointCloud<PointT>::ConstPtr& cloud ) const
    {
        if( !use_distance_filter ) {
            return cloud;
        }

        pcl::PointCloud<PointT>::Ptr filtered( new pcl::PointCloud<PointT>() );
        filtered->reserve( cloud->size() );

        std::copy_if( cloud->begin(), cloud->end(), std::back_inserter( filtered->points ), [&]( const PointT& p ) {
            double d = p.getVector3fMap().norm();
            return d > distance_near_thresh && d < distance_far_thresh;
        } );

        filtered->width    = filtered->size();
        filtered->height   = 1;
        filtered->is_dense = false;

        filtered->header = cloud->header;

        return filtered;
    }

    pcl::PointCloud<PointT>::Ptr deskewing( const pcl::PointCloud<PointT>::Ptr& cloud )
    {
        rclcpp::Time stamp = pcl_conversions::fromPCL( cloud->header.stamp );
        if( imu_queue.empty() ) {
            return cloud;
        }

        // the color encodes the point number in the point sequence
        if( colored_pub->get_subscription_count() ) {
            pcl::PointCloud<pcl::PointXYZRGB>::Ptr colored( new pcl::PointCloud<pcl::PointXYZRGB>() );
            colored->header   = cloud->header;
            colored->is_dense = cloud->is_dense;
            colored->width    = cloud->width;
            colored->height   = cloud->height;
            colored->resize( cloud->size() );

            for( int i = 0; i < (int)cloud->size(); i++ ) {
                double t                          = static_cast<double>( i ) / cloud->size();
                colored->at( i ).getVector4fMap() = cloud->at( i ).getVector4fMap();
                colored->at( i ).r                = 255 * t;
                colored->at( i ).g                = 128;
                colored->at( i ).b                = 255 * ( 1 - t );
            }
            sensor_msgs::msg::PointCloud2 colored_ros;
            pcl::toROSMsg( *colored, colored_ros );
            colored_pub->publish( colored_ros );
        }

        sensor_msgs::msg::Imu::ConstSharedPtr imu_msg = imu_queue.front();

        auto loc = imu_queue.begin();
        for( ; loc != imu_queue.end(); loc++ ) {
            imu_msg = ( *loc );
            if( rclcpp::Time( ( *loc )->header.stamp ) > stamp ) {
                break;
            }
        }

        imu_queue.erase( imu_queue.begin(), loc );

        Eigen::Vector3f ang_v( imu_msg->angular_velocity.x, imu_msg->angular_velocity.y, imu_msg->angular_velocity.z );
        ang_v *= -1;

        pcl::PointCloud<PointT>::Ptr deskewed( new pcl::PointCloud<PointT>() );
        deskewed->header   = cloud->header;
        deskewed->is_dense = cloud->is_dense;
        deskewed->width    = cloud->width;
        deskewed->height   = cloud->height;
        deskewed->resize( cloud->size() );

        for( int i = 0; i < (int)cloud->size(); i++ ) {
            const auto& pt = cloud->at( i );

            // TODO (from original repo): transform IMU data into the LIDAR frame
            double             delta_t = scan_period * static_cast<double>( i ) / cloud->size();
            Eigen::Quaternionf delta_q( 1, delta_t / 2.0 * ang_v[0], delta_t / 2.0 * ang_v[1], delta_t / 2.0 * ang_v[2] );
            Eigen::Vector3f    pt_ = delta_q.inverse() * pt.getVector3fMap();

            deskewed->at( i )                  = cloud->at( i );
            deskewed->at( i ).getVector3fMap() = pt_;
        }

        return deskewed;
    }

private:
    rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_sub;
    std::vector<sensor_msgs::msg::Imu::ConstSharedPtr>     imu_queue;

    rclcpp::Subscription<sensor_msgs::msg::PointCloud2>::SharedPtr points_sub;
    rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr    points_pub;

    rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr colored_pub;

    std::shared_ptr<tf2_ros::TransformListener> tf_listener;
    std::unique_ptr<tf2_ros::Buffer>            tf_buffer;

    // Algorithm, ROS2 parameters
    std::string downsample_method;
    double      downsample_resolution;

    std::string outlier_removal_method;
    int         statistical_mean_k;
    double      statistical_stddev_mul_thresh;
    double      radius_radius;
    int         radius_min_neighbors;

    bool   use_distance_filter;
    double distance_near_thresh;
    double distance_far_thresh;

    double      scan_period;
    bool        use_deskewing;
    std::string base_link_frame;

    pcl::Filter<PointT>::Ptr downsample_filter;
    pcl::Filter<PointT>::Ptr outlier_removal_filter;
};

}  // namespace mrg_slam

// Register the component with class_loader.
// This acts as a sort of entry point, allowing the component to be discoverable when its library
// is being loaded into a running process.
#include <rclcpp_components/register_node_macro.hpp>
RCLCPP_COMPONENTS_REGISTER_NODE( mrg_slam::PrefilteringComponent )
