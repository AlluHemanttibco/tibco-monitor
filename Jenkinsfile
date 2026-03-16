pipeline {
    // Run on the Linux machine where Python and the scripts are located
    agent { label 'SUSHQSTIB14' } 
    
    // --- 1. USER INTERFACE (Build with Parameters) ---
    parameters {
        choice(
            name: 'TARGET_ENV', 
            choices: ['STAGE', 'DEV'], 
            description: 'Select the TIBCO Environment to scan.'
        )
        string(
            name: 'TARGET_EARS', 
            defaultValue: 'OrderInfoREST, BWEnterprise, IPFeedEnterpriseFileAdapter', 
            description: 'Optional: Comma-separated list of EARs to check. Leave blank to scan ALL.'
        )
    }

    // --- 2. SCHEDULING ---
    triggers {
        // Run daily at 3:00 AM automatically
        cron('0 3 * * *') 
    }

    // --- 3. NON-SECRET ENVIRONMENT VARIABLES ---
    environment {
        SMTP_SERVER = 'smtp.urbanout.com' 
        ALERT_EMAIL = 'ven-hallu@urbn.com'
    }

    stages {
        stage('Checkout Code') {
            steps {
                // Pulls the Python script and config.json from your Git repo
                checkout scm
            }
        }
        
        stage('Run TIBCO Log Monitor') {
            steps {
                // --- 4. SECURE CREDENTIAL INJECTION ---
                withCredentials([
                    // SSH Key
                    sshUserPrivateKey(credentialsId: '33da6288-c83d-4585-99a1-ddd2b07e160b', keyFileVariable: 'SSH_KEY_PATH', usernameVariable: 'SSH_USER'),
                    
                    // FIXED: Using your admin's exact Slack ID
                    string(credentialsId: 'Jenikns-slack', variable: 'SLACK_WEBHOOK')
                ]) {
                    script {
                        echo "Starting TIBCO Log Scan for Environment: ${params.TARGET_ENV}"
                        if (params.TARGET_EARS != '') {
                            echo "Targeted EARs: ${params.TARGET_EARS}"
                        } else {
                            echo "Scanning all configured EARs."
                        }

                        // Execute the Python script
                        sh '''
                            export TARGET_ENV="${params.TARGET_ENV}"
                            export TARGET_EARS="${params.TARGET_EARS}"
                            
                            # Run the script
                            python3 tibco_monitor.py
                        '''
                    }
                }
            }
        }
    }

    // --- 5. POST-BUILD ACTIONS ---
    post {
        success {
            echo "✅ TIBCO Monitoring job completed successfully."
        }
        failure {
            // FIXED: Also updated the Slack ID here for failure notifications
            script {
                withCredentials([string(credentialsId: 'Jenikns-slack', variable: 'SLACK_WEBHOOK')]) {
                    sh '''
                        curl -X POST -H 'Content-type: application/json' \
                        --data '{"text":"❌ *CRITICAL:* Jenkins Job Failed to execute the TIBCO Monitor. Check Jenkins Console."}' \
                        $SLACK_WEBHOOK
                    '''
                }
            }
        }
    }
}
