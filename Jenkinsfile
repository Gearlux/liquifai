pipeline {
    agent any

    environment {
        // Local virtual environment within the Jenkins workspace for portability
        VENV_PATH = "${WORKSPACE}/.venv"
        VENV_BIN = "${VENV_PATH}/bin"
    }

    stages {
        stage('Initialize') {
            steps {
                echo 'Creating Isolated Virtual Environment...'
                sh "python3 -m venv ${VENV_PATH}"
                
                echo 'Installing Dependencies...'
                sh "${VENV_BIN}/pip install --upgrade pip"
                // Install trio dependencies from GitHub for CI portability
                sh "${VENV_BIN}/pip install git+https://github.com/gearlux/logflow.git"
                sh "${VENV_BIN}/pip install git+https://github.com/gearlux/confluid.git"
                sh "${VENV_BIN}/pip install -e .[dev]"
            }
        }

        stage('Quality Gates') {
            parallel {
                stage('Black') {
                    steps {
                        sh "${VENV_BIN}/black --check liquify tests examples"
                    }
                }
                stage('Isort') {
                    steps {
                        sh "${VENV_BIN}/isort --check-only liquify tests examples"
                    }
                }
                stage('Flake8') {
                    steps {
                        sh "rm -f flake8.txt flake8-report.xml"
                        sh "${VENV_BIN}/flake8 liquify tests examples --tee --output-file=flake8.txt || true"
                        sh "if [ -f flake8.txt ]; then ${VENV_BIN}/flake8_junit flake8.txt flake8-report.xml; fi"
                    }
                    post {
                        always {
                            junit allowEmptyResults: true, testResults: 'flake8-report.xml'
                        }
                    }
                }
                stage('Mypy') {
                    steps {
                        sh "${VENV_BIN}/mypy liquify tests examples"
                    }
                }
            }
        }

        stage('Unit Tests') {
            steps {
                sh "${VENV_BIN}/pytest tests --junitxml=test-report.xml --cov=liquify --cov-report=xml:coverage.xml --cov-report=term"
            }
            post {
                always {
                    // Archive and display JUnit test results
                    junit allowEmptyResults: true, testResults: 'test-report.xml'
                    
                    // Display Coverage in Jenkins UI using Code Coverage API Plugin
                    recordCoverage tools: [[parser: 'COBERTURA', pattern: 'coverage.xml']]
                }
            }
        }

        stage('Verify Examples') {
            steps {
                echo 'Running project examples...'
                // Use single quotes for the shell command to prevent Groovy from trying to resolve $f
                sh '''
                    for f in examples/*.py; do
                        echo "Verifying $f..."
                        ${VENV_BIN}/python3 "$f" --help
                    done
                '''
            }
        }
    }

    post {
        always {
            echo 'Liquify Pipeline Complete.'
        }
        success {
            echo 'Liquify is healthy and ready for publication.'
        }
        failure {
            echo 'Liquify build failed. Please check linting or test failures.'
        }
    }
}
