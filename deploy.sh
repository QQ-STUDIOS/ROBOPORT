#!/bin/bash
# ROBOPORT Auto-Deploy Script
# Run this from your local machine inside the ROBOPORT directory

set -e

echo "🚀 ROBOPORT Auto-Deploy"
echo "======================="
echo ""

# Check if we're in the right directory
if [ ! -d ".git" ]; then
    echo "❌ Error: Not in a git repository"
    echo "Please run this script from inside the ROBOPORT directory"
    exit 1
fi

# Configure git if needed
if ! git config user.email > /dev/null 2>&1; then
    echo "📝 Configuring git..."
    git config user.email "rustyrich020@users.noreply.github.com"
    git config user.name "RustyRich020"
fi

# Switch to initial-setup branch (create if doesn't exist)
echo "🔀 Switching to initial-setup branch..."
git checkout initial-setup 2>/dev/null || git checkout -b initial-setup

# Add all files
echo "📦 Staging all files..."
git add -A

# Commit
echo "💾 Committing changes..."
git commit -m "Complete ROBOPORT framework - Core agents, evaluation system, and workflows

- Core agent-os: Planner, Executor, Orchestrator, Critic
- Evaluation agents: Grader, Comparator, Analyzer
- Workflows: Execution flow and evaluation pipeline
- Schemas, documentation, and deployment guide
- Enterprise-grade structure ready for production" || echo "Nothing new to commit"

# Push
echo "⬆️  Pushing to GitHub..."
git push origin initial-setup

echo ""
echo "✅ Deploy complete!"
echo ""
echo "Next steps:"
echo "1. Go to: https://github.com/RustyRich020/ROBOPORT"
echo "2. Create a Pull Request: initial-setup → main"
echo "3. Merge and celebrate! 🎉"
