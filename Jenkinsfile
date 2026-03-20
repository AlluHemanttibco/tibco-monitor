pipeline {
    agent any 
    
    parameters {
        // NEW: Added 'ALL' to the top, and added all your production domains so you can select them manually.
        choice(name: 'TARGET_ENV', choices: ['ALL', 'STAGE', 'DEV', 'PHL_PCI', 'PHL_NPCI', 'DR_PCI', 'DR_NPCI'], description: 'Select Environment. ALL runs everything in config.')
        string(name: 'TARGET_EARS', defaultValue: '', description: 'Optional: Comma-separated list of EARs to check. Leave blank to scan ALL.')
    }

    triggers {
        // Remember: this runs once a day at 3:00 AM. 
        // If you want every 3 hours, use: cron('H */3 * * *')
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
                        
                        sh '''
                            export TARGET_ENV="$ENV_TO_SCAN"
                            export TARGET_EARS="$EARS_TO_SCAN"
                            
                            python3 -m pip install --user --upgrade pip setuptools wheel
                            python3 -m pip install --user cryptography==3.3.2 paramiko requests
                            
                            python3 tibco_monitor.py
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
                        echo "Slack notification is temporarily disabled."
                        # curl -X POST -H 'Content-type: application/json' \
                        # --data '{"text":"❌ *CRITICAL:* Jenkins Job Failed to execute the TIBCO Monitor."}' \
                        # "$SLACK_WEBHOOK"
                    '''
                }
            }
        }
    }
}
