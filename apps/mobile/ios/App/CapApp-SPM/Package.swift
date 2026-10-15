// swift-tools-version: 5.9
import PackageDescription

// DO NOT MODIFY THIS FILE - managed by Capacitor CLI commands
let package = Package(
    name: "CapApp-SPM",
    platforms: [.iOS(.v15)],
    products: [
        .library(
            name: "CapApp-SPM",
            targets: ["CapApp-SPM"])
    ],
    dependencies: [
        .package(url: "https://github.com/ionic-team/capacitor-swift-pm.git", exact: "8.5.2"),
        .package(name: "CapacitorCommunityKeepAwake", path: "symlinks/CapacitorCommunityKeepAwake"),
        .package(name: "CapacitorApp", path: "symlinks/CapacitorApp"),
        .package(name: "CapacitorAppLauncher", path: "symlinks/CapacitorAppLauncher"),
        .package(name: "CapacitorDevice", path: "symlinks/CapacitorDevice"),
        .package(name: "CapacitorHaptics", path: "symlinks/CapacitorHaptics"),
        .package(name: "CapacitorKeyboard", path: "symlinks/CapacitorKeyboard"),
        .package(name: "CapacitorLocalNotifications", path: "symlinks/CapacitorLocalNotifications"),
        .package(name: "CapacitorNetwork", path: "symlinks/CapacitorNetwork"),
        .package(name: "CapacitorPreferences", path: "symlinks/CapacitorPreferences"),
        .package(name: "CapacitorPushNotifications", path: "symlinks/CapacitorPushNotifications"),
        .package(name: "CapacitorSplashScreen", path: "symlinks/CapacitorSplashScreen"),
        .package(name: "CapacitorStatusBar", path: "symlinks/CapacitorStatusBar")
    ],
    targets: [
        .target(
            name: "CapApp-SPM",
            dependencies: [
                .product(name: "Capacitor", package: "capacitor-swift-pm"),
                .product(name: "Cordova", package: "capacitor-swift-pm"),
                .product(name: "CapacitorCommunityKeepAwake", package: "CapacitorCommunityKeepAwake"),
                .product(name: "CapacitorApp", package: "CapacitorApp"),
                .product(name: "CapacitorAppLauncher", package: "CapacitorAppLauncher"),
                .product(name: "CapacitorDevice", package: "CapacitorDevice"),
                .product(name: "CapacitorHaptics", package: "CapacitorHaptics"),
                .product(name: "CapacitorKeyboard", package: "CapacitorKeyboard"),
                .product(name: "CapacitorLocalNotifications", package: "CapacitorLocalNotifications"),
                .product(name: "CapacitorNetwork", package: "CapacitorNetwork"),
                .product(name: "CapacitorPreferences", package: "CapacitorPreferences"),
                .product(name: "CapacitorPushNotifications", package: "CapacitorPushNotifications"),
                .product(name: "CapacitorSplashScreen", package: "CapacitorSplashScreen"),
                .product(name: "CapacitorStatusBar", package: "CapacitorStatusBar")
            ]
        )
    ]
)
