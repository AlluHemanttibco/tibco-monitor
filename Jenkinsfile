pipeline {
    agent any 
    
    parameters {
        // NEW: Checkboxes for every environment! (Defaulted to true so the 3 AM job scans everything)
        booleanParam(name: 'SCAN_STAGE', defaultValue: true, description: 'Scan STAGE Environment')
        booleanParam(name: 'SCAN_DEV', defaultValue: true, description: 'Scan DEV Environment')
        booleanParam(name: 'SCAN_PHL_PCI', defaultValue: false, description: 'Scan PHL_PCI Environment')
        booleanParam(name: 'SCAN_PHL_NPCI', defaultValue: false, description: 'Scan PHL_NPCI Environment')
        booleanParam(name: 'SCAN_DR_PCI', defaultValue: false, description: 'Scan DR_PCI Environment')
        booleanParam(name: 'SCAN_DR_NPCI', defaultValue: false, description: 'Scan DR_NPCI Environment')
        
        string(name: 'TARGET_EARS', defaultValue: '', description: 'Optional: Comma-separated list of EARs to check. Leave blank to scan ALL.')
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
            steps {
                withCredentials([
                    usernamePassword(credentialsId: '33da6288-c83d-4585-99a1-ddd2b07e160b', usernameVariable: 'SSH_USER', passwordVariable: 'SSH_PASS'),
                    string(credentialsId: 'Jenikns-slack', variable: 'SLACK_WEBHOOK')
                ]) {
                    script {
                        // Dynamically build a list based on which checkboxes you ticked
                        def envList = []
                        if (params.SCAN_STAGE) envList.add('STAGE')
                        if (params.SCAN_DEV) envList.add('DEV')
                        if (params.SCAN_PHL_PCI) envList.add('PROD') // Using PROD to match your config.json temporarily, you can update this to PHL_PCI later!
                        if (params.SCAN_PHL_NPCI) envList.add('PHL_NPCI')
                        if (params.SCAN_DR_PCI) envList.add('DR_PCI')
                        if (params.SCAN_DR_NPCI) envList.add('DR_NPCI')
                        
                        // Save the list (e.g., "STAGE,DEV") to a safe environment variable
                        env.COMPILED_ENVS = envList.isEmpty() ? "ALL" : envList.join(',')
                        env.COMPILED_EARS = params.TARGET_EARS
                        
                        echo "Starting TIBCO Log Scan for Checked Environments: ${env.COMPILED_ENVS}"
                        
                        sh '''
                            export TARGET_ENV="$COMPILED_ENVS"
                            export TARGET_EARS="$COMPILED_EARS"
                            
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
                    '''
                }
            }
        }
    }
}
