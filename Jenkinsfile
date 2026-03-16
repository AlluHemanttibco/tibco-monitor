pipeline {
    agent any 
    
    parameters {
        choice(name: 'TARGET_ENV', choices: ['STAGE', 'DEV'], description: 'Select the TIBCO Environment to scan.')
        string(name: 'TARGET_EARS', defaultValue: 'OrderInfoREST, BWEnterprise, IPFeedEnterpriseFileAdapter', description: 'Optional: Comma-separated list of EARs to check.')
    }

    triggers {
        cron('0 3 * * *') 
    }

    environment {
        SMTP_SERVER = 'smtp.urbanout.com' 
        ALERT_EMAIL = 'ven-hallu@urbn.com'
    }

    stages {
        stage('Checkout Code') {
            steps {
                checkout scm
            }
        }
        
        stage('Run TIBCO Log Monitor') {
            // NEW: Safely map Jenkins parameters to environment variables
            environment {
                ENV_TO_SCAN = "${params.TARGET_ENV}"
                EARS_TO_SCAN = "${params.TARGET_EARS}"
            }
            steps {
                withCredentials([
                    usernamePassword(credentialsId: '33da6288-c83d-4585-99a1-ddd2b07e160b', usernameVariable: 'SSH_USER', passwordVariable: 'SSH_PASS'),
                    string(credentialsId: 'Jenikns-slack', variable: 'SLACK_WEBHOOK')
                ]) {
                    script {
                        echo "Starting TIBCO Log Scan for Environment: ${ENV_TO_SCAN}"
                        
                        // We use the new mapped variables here so Linux understands them
                        sh '''
                            export TARGET_ENV="$ENV_TO_SCAN"
                            export TARGET_EARS="$EARS_TO_SCAN"
                            
                            python3 tibco_log_monitor.py
                        '''
                    }
                }
            }
        }
    }

    post {
        success {
            echo "✅ TIBCO Monitoring job completed successfully."
        }
        failure {
            script {
                withCredentials([string(credentialsId: 'Jenikns-slack', variable: 'SLACK_WEBHOOK')]) {
                    sh '''
                        curl -X POST -H 'Content-type: application/json' \
                        --data '{"text":"❌ *CRITICAL:* Jenkins Job Failed to execute the TIBCO Monitor. Check Jenkins Console."}' \
                        "$SLACK_WEBHOOK"
                    '''
                }
            }
        }
    }
}
