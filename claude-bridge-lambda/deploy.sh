#!/bin/bash

# Create a temporary directory for the deployment package
mkdir package
pip install -r requirements.txt --platform manylinux2014_x86_64 -t package --no-cache-dir --implementation cp  --only-binary=:all: 

# Create deployment package
cd package
zip -r ../lambda_deployment_package.zip .
cd ..
zip lambda_deployment_package.zip lambda_function.py

# Update Lambda function
aws lambda update-function-code \
    --function-name ClaudeBridge3 \
    --zip-file fileb://lambda_deployment_package.zip

# Clean up
rm -rf package
rm lambda_deployment_package.zip

echo "Lambda function updated successfully!"