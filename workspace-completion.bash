#!/usr/bin/env bash
# Bash completion for workspace.sh
# Source this file to enable tab completion:
#   source workspace-completion.bash
# Or add to ~/.bashrc:
#   source /path/to/workspace-completion.bash

_workspace_sh_completion() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Main commands
    commands="download clips voice transcribe stitch dl-subs dates gpu extra-utils info help"

    # If we're completing the first argument
    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "$commands" -- "$cur") )
        return 0
    fi

    # Subcommands based on main command
    case "${COMP_WORDS[1]}" in
        download|dl)
            # Complete with files in data/ directory
            COMPREPLY=( $(compgen -f -X '!*.txt' -- "$cur") )
            ;;
        clips|clip)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "hits cut-local cut-net refine help" -- "$cur") )
            fi
            ;;
        voice|filter)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "filter filter-simple filter-parallel filter-chunked extract" -- "$cur") )
            fi
            ;;
        transcribe|trans)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "--model --language --force --outfmt --follow --no-follow --setup-venvs --help" -- "$cur") )
            fi
            ;;
        stitch|concat)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "batch cfr simple" -- "$cur") )
            fi
            ;;
        dl-subs)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "subs-only batch from-dir" -- "$cur") )
            elif [[ $COMP_CWORD -eq 3 && "${COMP_WORDS[2]}" == "from-dir" ]]; then
                COMPREPLY=( $(compgen -d -- "$cur") )
            fi
            ;;
        dates|date)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "find-missing create-list move compare" -- "$cur") )
            fi
            ;;
        extra-utils)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "help mark_success.sh quality_report.py repair_archive.sh map_ids_to_files.py sort_clips.py concat_filter_from_list.sh stitch_videos_batched_filter.sh detect_dupe_hallu.py watch_cuda_error.sh" -- "$cur") )
            fi
            ;;
        gpu)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "status to-nvidia to-vfio" -- "$cur") )
            fi
            ;;
        help)
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "$commands" -- "$cur") )
            fi
            ;;
    esac

    return 0
}

complete -F _workspace_sh_completion workspace.sh
complete -F _workspace_sh_completion ./workspace.sh

echo "Tab completion enabled for workspace.sh"
echo "Try: ./workspace.sh <TAB><TAB>"
