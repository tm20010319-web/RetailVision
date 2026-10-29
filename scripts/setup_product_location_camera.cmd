@echo off
title RetailVision Camera Login
powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0setup_product_location_camera.ps1" %*
