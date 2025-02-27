import json
import os
import boto3
import anthropic
from datetime import datetime
from uuid import uuid4
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any

class ProjectGenerator:
    def __init__(self):
        print("Initializing ProjectGenerator...")
        self._init_claude()
        self._init_aws_resources()
        self.system_prompt = "You are a full stack software developer proficient in designing and implementing on AWS"
        self.max_context_tokens = 3000
        print("ProjectGenerator initialized successfully")

    def _init_claude(self):
        """Initialize Claude client with API key from Secrets Manager"""
        print("Initializing Claude client...")
        secret_name = "anthropic_api_key"
        region_name = "us-east-1"

        session = boto3.session.Session()
        client = session.client(
            service_name='secretsmanager',
            region_name=region_name
        )

        try:
            get_secret_value_response = client.get_secret_value(
                SecretId=secret_name
            )
            secret = json.loads(get_secret_value_response['SecretString'])['ANTHROPIC_API_KEY']
            self.claude = anthropic.Client(api_key=secret)
            print("Claude client initialized successfully")
        except ClientError as e:
            print(f"Error getting secret: {e}")
            raise

    def _init_aws_resources(self):
        """Initialize AWS resource clients and table references"""
        print("Initializing AWS resources...")
        self.dynamodb = boto3.resource('dynamodb')
        self.s3 = boto3.client('s3')
        
        self.users_table = self.dynamodb.Table('Users')
        self.projects_table = self.dynamodb.Table('Projects')
        self.conversation_bucket = 'project_conversation_context'
        print(f"AWS resources initialized: Users table, Projects table, and S3 bucket {self.conversation_bucket}")

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch user from DynamoDB"""
        print(f"Fetching user with ID: {user_id}")
        try:
            response = self.users_table.get_item(
                Key={'userId': user_id}
            )
            user = response.get('Item')
            if user:
                print(f"User found: {user_id}")
            else:
                print(f"User not found: {user_id}")
            return user
        except ClientError as e:
            print(f"Error fetching user: {e}")
            return None

    def create_user(self, user_id: str) -> Dict[str, Any]:
        """Create new user in DynamoDB"""
        print(f"Creating new user with ID: {user_id}")
        user_item = {
            'userId': user_id,
            'createdAt': datetime.utcnow().isoformat(),
            'status': 'ACTIVE'
        }
        try:
            self.users_table.put_item(Item=user_item)
            print(f"User created successfully: {user_id}")
            return user_item
        except ClientError as e:
            print(f"Error creating user: {e}")
            raise

    def get_or_create_user(self, user_id: str) -> Dict[str, Any]:
        """Get existing user or create new one"""
        print(f"Getting or creating user: {user_id}")
        user = self.get_user(user_id)
        if not user:
            user = self.create_user(user_id)
        return user

    def get_project(self, project_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch project from DynamoDB"""
        print(f"Fetching project: {project_id} for user: {user_id}")
        try:
            response = self.projects_table.get_item(
                Key={
                    'projectId': project_id,
                    'userId': user_id
                }
            )
            project = response.get('Item')
            if project:
                print(f"Project found: {project_id}")
            else:
                print(f"Project not found: {project_id}")
            return project
        except ClientError as e:
            print(f"Error fetching project: {e}")
            return None

    def get_conversation_context(self, user_id: str, project_id: str) -> Dict[str, Any]:
        """Get conversation context from S3 or create new one"""
        s3_key = f'{user_id}/{project_id}/conversation.json'
        print(f"Getting conversation context from S3: {s3_key}")
        
        try:
            # Try to get existing conversation
            response = self.s3.get_object(
                Bucket=self.conversation_bucket,
                Key=s3_key
            )
            conversation = json.loads(response['Body'].read())
            print(f"Existing conversation found with {len(conversation['messages'])} messages")
            
            # Estimate tokens (rough approximation)
            total_tokens = sum(len(msg['content'].split()) * 1.3 for msg in conversation['messages'])
            print(f"Estimated token count in conversation: {total_tokens}")
            
            # Trim to stay within token limit
            if total_tokens > self.max_context_tokens:
                print(f"Trimming conversation to stay within {self.max_context_tokens} token limit")
                # Start removing oldest messages until we're under the limit
                while total_tokens > self.max_context_tokens and len(conversation['messages']) > 2:
                    removed_msg = conversation['messages'].pop(0)
                    removed_tokens = len(removed_msg['content'].split()) * 1.3
                    total_tokens -= removed_tokens
                    print(f"Removed message with ~{removed_tokens} tokens, new total: {total_tokens}")
            
            return conversation
        except self.s3.exceptions.NoSuchKey:
            # Create new conversation if it doesn't exist
            print(f"No existing conversation found, creating new one")
            return {
                'messages': [],
                'createdAt': datetime.utcnow().isoformat(),
                'updatedAt': datetime.utcnow().isoformat()
            }
        except ClientError as e:
            print(f"Error getting conversation context: {e}")
            raise

    def append_conversation(self, user_id: str, project_id: str, new_message: dict) -> Dict[str, Any]:
        """Append to conversation in S3"""
        s3_key = f'{user_id}/{project_id}/conversation.json'
        print(f"Appending message to conversation: {s3_key}")
        print(f"Message role: {new_message['role']}, content length: {len(new_message['content'])}")
        
        try:
            # Get existing conversation or create new one
            conversation = self.get_conversation_context(user_id, project_id)
            
            # Append new message
            conversation['messages'].append(new_message)
            conversation['updatedAt'] = datetime.utcnow().isoformat()
            
            # Store updated conversation
            print(f"Storing updated conversation with {len(conversation['messages'])} messages")
            self.s3.put_object(
                Bucket=self.conversation_bucket,
                Key=s3_key,
                Body=json.dumps(conversation)
            )

            # Update S3 location in projects table if it's the first message
            if len(conversation['messages']) == 1:
                print(f"First message in conversation, updating project with S3 location")
                self.projects_table.update_item(
                    Key={
                        'projectId': project_id,
                        'userId': user_id
                    },
                    UpdateExpression='SET conversationLocation = :loc, updatedAt = :timestamp',
                    ExpressionAttributeValues={
                        ':loc': f's3://{self.conversation_bucket}/{s3_key}',
                        ':timestamp': datetime.utcnow().isoformat()
                    }
                )
                print(f"Project updated with conversation location")

            return conversation
        except ClientError as e:
            print(f"Error handling conversation: {e}")
            raise

    def update_project_status(self, project_id: str, user_id: str, status: str):
        """Update project status in DynamoDB"""
        print(f"Updating project status: {project_id} to {status}")
        try:
            self.projects_table.update_item(
                Key={
                    'projectId': project_id,
                    'userId': user_id
                },
                UpdateExpression='SET #status = :status, updatedAt = :timestamp',
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':status': status,
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
            print(f"Project status updated successfully")
        except ClientError as e:
            print(f"Error updating project status: {e}")
            raise

    def generate_response(self, user_id: str, project_id: str, user_input: str) -> str:
        """Generate response using Claude with conversation context"""
        print(f"Generating Claude response for project: {project_id}")
        
        # Get conversation context
        # conversation = self.get_conversation_context(user_id, project_id)
        conversation = {'messages':[]}
        
        # Prepare messages for Claude
        claude_messages = []
        
        # Add previous messages from context
        for msg in conversation['messages']:
            claude_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # Add new user message
        claude_messages.append({
            "role": "user",
            "content": user_input
        })
        
        print(f"Sending request to Claude with {len(claude_messages)} messages")
        try:
            # Call Claude API
            response = self.claude.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4096,
                system=self.system_prompt,
                messages=claude_messages
            )
            
            # Get assistant's response
            assistant_response = response.content[0].text
            print(f"Received response from Claude, ", response.content)
            
            # Add assistant's response to conversation
            self.append_conversation(
                user_id,
                project_id,
                {
                    "role": "assistant",
                    "content": assistant_response,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            return assistant_response
        except Exception as e:
            print(f"Error generating response: {e}")
            raise

def lambda_handler(event, context):
    print(f"Lambda invoked with event: {json.dumps(event)}")
    try:
        # Validate input
        if 'userId' not in event:
            print("Error: userId is required")
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'userId is required'})
            }
        
        if 'input' not in event:
            print("Error: user input is required")
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'user input is required'})
            }

        user_id = event['userId']
        user_input = event['input']
        project_id = event.get('projectId', str(uuid4()))
        is_new_project = 'projectId' not in event
        
        print(f"Processing request for user: {user_id}, project: {project_id}, new project: {is_new_project}")

        generator = ProjectGenerator()
        
        # Validate/create user
        # user = generator.get_or_create_user(user_id)

        # For new projects, create project record
        # if is_new_project:
        #     print(f"Creating new project: {project_id}")
        #     generator.projects_table.put_item(
        #         Item={
        #             'projectId': project_id,
        #             'userId': user_id,
        #             'description': user_input[:100],  # First 100 chars as description
        #             'status': 'IN_PROGRESS',
        #             'createdAt': datetime.utcnow().isoformat(),
        #             'updatedAt': datetime.utcnow().isoformat()
        #         }
        #     )
        #     print(f"New project created successfully")
        
        # Add user message to conversation
        # print(f"Adding user message to conversation")
        # generator.append_conversation(
        #     user_id,
        #     project_id,
        #     {
        #         "role": "user",
        #         "content": user_input,
        #         "timestamp": datetime.utcnow().isoformat()
        #     }
        # )
        
        # Generate response
        print(f"Generating response from Claude")
        assistant_response = generator.generate_response(user_id, project_id, user_input)
        
        print(f"Request processed successfully, returning response")
        return {
            'statusCode': 200,
            'body': json.dumps({
                'projectId': project_id,
                'response': assistant_response
            })
        }

    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal server error: {str(e)}'
            })
        }
    
if __name__ == '__main__':
    test_event = {'userId': 'test-user-123', 'input': 'I want to build a simple todo app with React frontend and Python backend'}
    lambda_handler(test_event, {})
