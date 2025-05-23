# Copyright (c) 2018-2022, NVIDIA Corporation
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# docs and experiment results can be found at https://docs.cleanrl.dev/rl-algorithms/ppo/#ppo_continuous_action_isaacgympy
import argparse
import os
import random
import time
from distutils.util import strtobool

import gym
import isaacgym  # noqa
import isaacgymenvs
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions.normal import Normal
from torch.utils.tensorboard import SummaryWriter
from collections import deque
from gym.wrappers.normalize import RunningMeanStd
from copy import deepcopy
import csv
import gc

def parse_args():
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp-name", type=str, default=os.path.basename(__file__).rstrip(".py"),
        help="the name of this experiment")
    parser.add_argument("--seed", type=int, default=11,
        help="seed of the experiment")
    parser.add_argument("--torch-deterministic", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, `torch.backends.cudnn.deterministic=False`")
    parser.add_argument("--cuda", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, cuda will be enabled by default")
    parser.add_argument("--track", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="if toggled, this experiment will be tracked with Weights and Biases")
    parser.add_argument("--wandb-project-name", type=str, default="randomized-exploration-20250425",
        help="the wandb's project name")
    parser.add_argument("--wandb-entity", type=str, default="",
        help="the entity (team) of wandb's project")
    parser.add_argument("--capture-video", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
        help="whether to capture videos of the agent performances (check out `videos` folder)")
    parser.add_argument("--gpu-id", type=int, default=0,
        help="ID of GPU to use")

    # Algorithm specific arguments AnymalTerrain
    parser.add_argument("--env-id", type=str, default="Humanoid",
        help="the id of the environment")
    parser.add_argument("--total-timesteps", type=int, default=300000000,
        help="total timesteps of the experiments")
    parser.add_argument("--learning-rate", type=float, default=0.0026,
        help="the learning rate of the optimizer")
    parser.add_argument("--num-envs", type=int, default=4096,
        help="the number of parallel game environments")
    parser.add_argument("--num-steps", type=int, default=16,
        help="the number of steps to run in each environment per policy rollout")
    parser.add_argument("--anneal-lr", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
        help="Toggle learning rate annealing for policy and value networks")
    parser.add_argument("--gamma", type=float, default=0.99,
        help="the discount factor gamma")
    parser.add_argument("--gae-lambda", type=float, default=0.95,
        help="the lambda for the general advantage estimation")
    parser.add_argument("--num-minibatches", type=int, default=2,
        help="the number of mini-batches")
    parser.add_argument("--update-epochs", type=int, default=4,
        help="the K epochs to update the policy")
    parser.add_argument("--norm-adv", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
        help="Toggles advantages normalization")
    parser.add_argument("--clip-coef", type=float, default=0.2,
        help="the surrogate clipping coefficient")
    parser.add_argument("--clip-vloss", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
        help="Toggles whether or not to use a clipped loss for the value function, as per the paper.")
    parser.add_argument("--ent-coef", type=float, default=0.0,
        help="coefficient of the entropy")
    parser.add_argument("--vf-coef", type=float, default=2,
        help="coefficient of the value function")
    parser.add_argument("--max-grad-norm", type=float, default=1,
        help="the maximum norm for the gradient clipping")
    parser.add_argument("--target-kl", type=float, default=None,
        help="the target KL divergence threshold")

    parser.add_argument("--reward-scaler", type=float, default=1,
        help="the scale factor applied to the reward during training")
    parser.add_argument("--record-video-step-frequency", type=int, default=1464,
        help="the frequency at which to record the videos")
    parser.add_argument("--save_model", type=bool, default=True, help="if save model")

    parser.add_argument("--add_noise", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
                        help="if add noise")
    parser.add_argument("--bi_noise", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
                        help="if add bi-noise")
    parser.add_argument("--random-noise", type=bool, default=False, help="if add noise")

    parser.add_argument("--rate", type=float, default=0.1, help="top-k")
    parser.add_argument("--std", type=float, default=1, help="noise std")
    parser.add_argument("--mean", type=float, default=0, help="noise std")
    parser.add_argument("--decay_scale", type=float, default=0.5)
    parser.add_argument("--noise_w", type=float, default=0.1, help="noise weight")

    args = parser.parse_args()
    args.batch_size = int(args.num_envs * args.num_steps)
    args.minibatch_size = int(args.batch_size // args.num_minibatches)
    # fmt: on
    return args


class RecordEpisodeStatisticsTorch(gym.Wrapper):
    def __init__(self, env, device):
        super().__init__(env)
        self.num_envs = getattr(env, "num_envs", 1)
        self.device = device
        self.episode_returns = None
        self.episode_lengths = None

    def reset(self, **kwargs):
        observations = super().reset(**kwargs)
        self.episode_returns = torch.zeros(self.num_envs, dtype=torch.float32, device=self.device)
        self.ground_truth_episode_returns = torch.zeros(self.num_envs, dtype=torch.float32, device=self.device)
        self.episode_lengths = torch.zeros(self.num_envs, dtype=torch.float32, device=self.device)
        self.returned_episode_returns = torch.zeros(self.num_envs, dtype=torch.float32, device=self.device)
        self.returned_ground_truth_episode_returns = torch.zeros(self.num_envs, dtype=torch.float32, device=self.device)
        self.returned_episode_lengths = torch.zeros(self.num_envs, dtype=torch.float32, device=self.device)
        return observations

    def step(self, action):
        observations, rewards, dones, infos = super().step(action)
        self.episode_returns += rewards
        self.ground_truth_episode_returns += infos["true_rewards"]
        self.episode_lengths += 1
        self.returned_episode_returns[:] = self.episode_returns
        self.returned_ground_truth_episode_returns[:] = self.ground_truth_episode_returns
        self.returned_episode_lengths[:] = self.episode_lengths
        self.episode_returns *= 1 - dones.float()
        self.ground_truth_episode_returns *= 1 - dones.float()
        self.episode_lengths *= 1 - dones.float()
        infos["r"] = self.returned_episode_returns
        infos["ground_truth_r"] = self.returned_ground_truth_episode_returns
        infos["l"] = self.returned_episode_lengths
        return (
            observations,
            rewards,
            dones,
            infos,
        )


def layer_init(layer, std=np.sqrt(2), bias_const=0.0):
    torch.nn.init.orthogonal_(layer.weight, std)
    torch.nn.init.constant_(layer.bias, bias_const)
    return layer


class Agent(nn.Module):
    def __init__(self, envs):
        super().__init__()
        self.critic = nn.Sequential(
            layer_init(nn.Linear(np.array(envs.single_observation_space.shape).prod(), 256)),
            nn.Tanh(),
            layer_init(nn.Linear(256, 256)),
            nn.Tanh(),
            layer_init(nn.Linear(256, 1), std=1.0),
        )

        self.actor_mean = nn.Sequential(
            layer_init(nn.Linear(np.array(envs.single_observation_space.shape).prod(), 256)),
            nn.Tanh(),
            layer_init(nn.Linear(256, 256)),
            nn.Tanh(),
            layer_init(nn.Linear(256, np.prod(envs.single_action_space.shape)), std=0.01),
        )
        self.actor_logstd = nn.Parameter(torch.zeros(1, np.prod(envs.single_action_space.shape)))

    def get_value(self, x):
        return self.critic(x)

    def get_action_and_value(self, x, action=None):
        action_mean = self.actor_mean(x)
        action_logstd = self.actor_logstd.expand_as(action_mean)
        action_std = torch.exp(action_logstd)
        probs = Normal(action_mean, action_std)
        if action is None:
            action = probs.sample()
        return action, probs.log_prob(action).sum(1), probs.entropy().sum(1), self.critic(x)

    def save_checkpoint(self, path):
        # Save step, model and optimizer states
        ckpt_dict = dict(
            actor = self.critic.state_dict(),
            critic = self.actor_mean.state_dict(),
            # opt = self.actor_logstd.state_dict()
        )
        torch.save(ckpt_dict, path)
        # print(f'Save checkpoint at step={step}')

class ExtractObsWrapper(gym.ObservationWrapper):
    def observation(self, obs):
        return obs["obs"]

class RewardForwardFilter:
    def __init__(self, gamma):
        self.rewems = None
        self.gamma = gamma

    def update(self, rews, not_done=None):
        if not_done is None:
            if self.rewems is None:
                self.rewems = rews
            else:
                self.rewems = self.rewems * self.gamma + rews
            return self.rewems
        else:
            if self.rewems is None:
                self.rewems = rews
            else:
                mask = np.where(not_done == 1.0)
                self.rewems[mask] = self.rewems[mask] * self.gamma + rews[mask]
            return deepcopy(self.rewems)



if __name__ == "__main__":
    args = parse_args()
    run_name = f"202505_{args.env_id}__{args.exp_name}__{args.seed}_lr{args.learning_rate}_{int(time.time())}"
    if args.add_noise:
        if args.random_noise:
            run_name += "-random"
        if not args.bi_noise:
            run_name += f"-max-uncertainty-rate{args.rate}"
        else:
            run_name += f"-bi"
    if args.track:
        import wandb

        wandb.init(
            project=args.wandb_project_name,
            entity=args.wandb_entity,
            # sync_tensorboard=True,
            config=vars(args),
            name=run_name,
            # monitor_gym=True,
            save_code=True,
        )

    if args.save_model:
        log_path = f"results/{run_name}"
        model_path = f"results/{run_name}/models"
        if not os.path.exists(model_path):
            os.makedirs(model_path, exist_ok=False)

    # TRY NOT TO MODIFY: seeding
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cudnn.deterministic = args.torch_deterministic

    device = torch.device("cuda" if torch.cuda.is_available() and args.cuda else "cpu")

    # env setup
    envs = isaacgymenvs.make(
        seed=args.seed,
        task=args.env_id,
        num_envs=args.num_envs,
        sim_device=f"cuda:{args.gpu_id}" if torch.cuda.is_available() and args.cuda else "cpu",
        rl_device=f"cuda:{args.gpu_id}" if torch.cuda.is_available() and args.cuda else "cpu",
        graphics_device_id=0 if torch.cuda.is_available() and args.cuda else -1,
        headless=True if torch.cuda.is_available() and args.cuda else True,
        multi_gpu=False,
        virtual_screen_capture=args.capture_video,
        force_render=False,
    )
    if args.capture_video:
        envs.is_vector_env = True
        print(f"record_video_step_frequency={args.record_video_step_frequency}")
        envs = gym.wrappers.RecordVideo(
            envs,
            f"videos/{run_name}",
            step_trigger=lambda step: step % args.record_video_step_frequency == 0,
            video_length=100,  # for each video record up to 100 steps
        )
    envs = ExtractObsWrapper(envs)
    envs = RecordEpisodeStatisticsTorch(envs, device)
    envs.single_action_space = envs.action_space
    envs.single_observation_space = envs.observation_space
    assert isinstance(envs.single_action_space, gym.spaces.Box), "only continuous action space is supported"

    agent = Agent(envs).to(device)
    optimizer = optim.Adam(agent.parameters(), lr=args.learning_rate, eps=1e-5)

    ext_reward_rms = RunningMeanStd()
    ext_discounted_reward = RewardForwardFilter(args.gamma)

    # ALGO Logic: Storage setup
    obs = torch.zeros((args.num_steps, args.num_envs) + envs.single_observation_space.shape, dtype=torch.float).to(device)
    actions = torch.zeros((args.num_steps, args.num_envs) + envs.single_action_space.shape, dtype=torch.float).to(device)
    logprobs = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    rewards = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    dones = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    values = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    advantages = torch.zeros_like(rewards, dtype=torch.float).to(device)
    true_rewards = torch.zeros_like(rewards, dtype=torch.float).to(device)

    # Logging setup
    num_done_envs = 512
    avg_returns = deque(maxlen=num_done_envs)
    avg_ep_lens = deque(maxlen=num_done_envs)
    avg_consecutive_successes = deque(maxlen=num_done_envs)
    avg_true_returns = deque(maxlen=num_done_envs) # returns from without reward shaping

    # TRY NOT TO MODIFY: start the game
    global_step = 0
    start_time = time.time()
    next_obs = envs.reset()
    next_done = torch.zeros(args.num_envs, dtype=torch.float).to(device)
    num_updates = args.total_timesteps // args.batch_size
    max_reward_visit = [-999, -999]
    for update in range(1, num_updates + 1):
        it_start_time = time.time()
        # Annealing the rate if instructed to do so.
        if args.anneal_lr:
            frac = 1.0 - (update - 1.0) / num_updates
            lrnow = frac * args.learning_rate
            optimizer.param_groups[0]["lr"] = lrnow

        for step in range(0, args.num_steps):
            global_step += 1 * args.num_envs
            obs[step] = next_obs
            dones[step] = next_done

            # ALGO LOGIC: action logic
            with torch.no_grad():
                action, logprob, _, value = agent.get_action_and_value(next_obs)
                values[step] = value.flatten()
            actions[step] = action
            logprobs[step] = logprob

            # TRY NOT TO MODIFY: execute the game and log data.
            next_obs, rewards[step], next_done, info = envs.step(action)
            true_rewards[step] = info["true_rewards"]

            for idx, d in enumerate(next_done):
                if d:
                    episodic_return = info["r"][idx].item()
                    true_return = info["ground_truth_r"][idx].item()
                    avg_returns.append(info["r"][idx].item())
                    avg_true_returns.append(info["ground_truth_r"][idx].item())
                    avg_ep_lens.append(info["l"][idx].item())
                    if "consecutive_successes" in info:  # ShadowHand and AllegroHand metric
                        avg_consecutive_successes.append(info["consecutive_successes"].item())

        not_dones = (1.0 - dones).cpu().data.numpy()
        rewards_cpu = rewards.cpu().data.numpy()
        ext_reward_per_env = np.array(
            [ext_discounted_reward.update(rewards_cpu[i], not_dones[i]) for i in range(args.num_steps)]
        )
        ext_reward_rms.update(ext_reward_per_env.flatten())
        rewards /= np.sqrt(ext_reward_rms.var)

        # bootstrap value if not done
        with torch.no_grad():
            next_value = agent.get_value(next_obs).reshape(1, -1)
            advantages = torch.zeros_like(rewards).to(device)
            lastgaelam = 0
            for t in reversed(range(args.num_steps)):
                if t == args.num_steps - 1:
                    nextnonterminal = 1.0 - next_done.float()
                    nextvalues = next_value
                else:
                    nextnonterminal = 1.0 - dones[t + 1]
                    nextvalues = values[t + 1]
                delta = rewards[t] + args.gamma * nextvalues * nextnonterminal - values[t]
                advantages[t] = lastgaelam = delta + args.gamma * args.gae_lambda * nextnonterminal * lastgaelam
            returns = advantages + values

        # flatten the batch
        b_obs = obs.reshape((-1,) + envs.single_observation_space.shape)
        b_logprobs = logprobs.reshape(-1)
        b_actions = actions.reshape((-1,) + envs.single_action_space.shape)
        b_advantages = advantages.reshape(-1)
        b_returns = returns.reshape(-1)
        b_values = values.reshape(-1)

        # Optimizing the policy and value network
        clipfracs = []
        for epoch in range(args.update_epochs):
            b_inds = torch.randperm(args.batch_size, device=device)
            for start in range(0, args.batch_size, args.minibatch_size):
                end = start + args.minibatch_size
                mb_inds = b_inds[start:end]

                _, newlogprob, entropy, newvalue = agent.get_action_and_value(b_obs[mb_inds], b_actions[mb_inds])
                logratio = newlogprob - b_logprobs[mb_inds]
                ratio = logratio.exp()

                with torch.no_grad():
                    # calculate approx_kl http://joschu.net/blog/kl-approx.html
                    old_approx_kl = (-logratio).mean()
                    approx_kl = ((ratio - 1) - logratio).mean()
                    clipfracs += [((ratio - 1.0).abs() > args.clip_coef).float().mean().item()]

                mb_advantages = b_advantages[mb_inds]
                if args.add_noise:
                    newvalue = newvalue.view(-1)
                    td = newvalue - b_returns[mb_inds]
                    _, sorted_indices = torch.sort(torch.flatten(torch.abs(td)), descending=True)  # 升序

                    num = int(len(mb_inds) * args.rate)
                    top_k_indices = sorted_indices[:num]
                    b_returns_select = b_returns[mb_inds]
                    noise = torch.randn_like(b_returns_select) * args.std + args.mean
                    if args.bi_noise:
                        mean_reward_mask = torch.mean(b_returns_select)
                        mask_greater_than_mean = (b_returns_select > mean_reward_mask) & (torch.flatten(td) < 0)

                        mask_less_than_mean = (b_returns_select <= mean_reward_mask) & (torch.flatten(td) < 0)

                        noise[mask_greater_than_mean] = -abs(noise[mask_greater_than_mean])
                        noise[mask_less_than_mean] = abs(noise[mask_less_than_mean])
                        noise[~(mask_greater_than_mean | mask_less_than_mean)] = 0

                    noise = torch.clamp(noise, max=1, min=-1)
                    scale = args.noise_w * torch.exp(-torch.tensor(update / (num_updates*args.decay_scale)) ** 2)
                    b_returns_select[top_k_indices] += scale * noise[top_k_indices]
                    b_returns[mb_inds] = b_returns_select
                    new_td = newvalue - b_returns[mb_inds]
                    td_dis_after_noise = (new_td - td).mean()
                    mb_advantages[top_k_indices] += scale * noise[top_k_indices]


                if args.norm_adv:
                    mb_advantages = (mb_advantages - mb_advantages.mean()) / (mb_advantages.std() + 1e-8)

                # Policy loss
                pg_loss1 = -mb_advantages * ratio
                pg_loss2 = -mb_advantages * torch.clamp(ratio, 1 - args.clip_coef, 1 + args.clip_coef)
                pg_loss = torch.max(pg_loss1, pg_loss2).mean()


                # Value loss
                newvalue = newvalue.view(-1)
                if args.clip_vloss:
                    v_loss_unclipped = (newvalue - b_returns[mb_inds]) ** 2
                    v_clipped = b_values[mb_inds] + torch.clamp(
                        newvalue - b_values[mb_inds],
                        -args.clip_coef,
                        args.clip_coef,
                    )
                    v_loss_clipped = (v_clipped - b_returns[mb_inds]) ** 2
                    v_loss_max = torch.max(v_loss_unclipped, v_loss_clipped)
                    v_loss = 0.5 * v_loss_max.mean()
                else:
                    v_loss = 0.5 * ((newvalue - b_returns[mb_inds]) ** 2).mean()

                entropy_loss = entropy.mean()
                loss = pg_loss - args.ent_coef * entropy_loss + v_loss * args.vf_coef

                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(agent.parameters(), args.max_grad_norm)
                optimizer.step()

            if args.target_kl is not None:
                if approx_kl > args.target_kl:
                    break
        
        it_end_time = time.time()

        # TRY NOT TO MODIFY: record rewards for plotting purposes
        data = {}
        # print("SPS:", int(global_step / (time.time() - start_time)))

        data["charts/iterations"] = update
        data["charts/learning_rate"] = optimizer.param_groups[0]["lr"]
        data["losses/value_loss"] = v_loss.item()
        data["losses/policy_loss"] = pg_loss.item()
        data["losses/entropy"] = entropy_loss.item()
        data["losses/old_approx_kl"] = old_approx_kl.item()
        data["losses/clipfrac"] = np.mean(clipfracs)
        data["losses/approx_kl"] = approx_kl.item()
        data["losses/all_loss"] = loss.item()
        data["charts/SPS"] = int(global_step / (time.time() - start_time))

        data["rewards/rewards_mean"] = rewards.mean().item()
        data["rewards/rewards_max"] = rewards.max().item()
        data["rewards/rewards_min"] = rewards.min().item()
        data["rewards/true_rewards_mean"] = true_rewards.mean().item()
        data["rewards/true_rewards_max"] = true_rewards.max().item()
        data["rewards/true_rewards_min"] = true_rewards.min().item()

        data["returns/advantages"] = b_advantages.mean().item()
        data["returns/ret_ext"] = b_returns.mean().item()
        data["returns/values_ext"] = b_values.mean().item()

        data["charts/traj_len"] = np.mean(avg_ep_lens)
        data["charts/max_traj_len"] = np.max(avg_ep_lens, initial=0)
        data["charts/min_traj_len"] = np.min(avg_ep_lens, initial=0)
        data["charts/time_per_it"] = it_end_time - it_start_time
        data["charts/episode_return"] = np.mean(avg_returns)
        data["charts/max_episode_return"] = np.max(avg_returns, initial=0)
        data["charts/min_episode_return"] = np.min(avg_returns, initial=0)
        data["charts/true_episode_return"] = np.mean(avg_true_returns)
        data["charts/max_true_episode_return"] = np.max(avg_true_returns, initial=0)
        data["charts/min_true_episode_return"] = np.min(avg_true_returns, initial=0)

        data["charts/consecutive_successes"] = np.mean(avg_consecutive_successes)
        data["charts/max_consecutive_successes"] = np.max(avg_consecutive_successes, initial=0)
        data["charts/min_consecutive_successes"] = np.min(avg_consecutive_successes, initial=0)

        if args.track:
            wandb.log(data, step=global_step)
            # 写入 CSV 文件
            log_data = {

            }

    # envs.close()
    if args.track:
        wandb.finish()
    # writer.close()
    if args.save_model:
        torch.save(agent.state_dict(), os.path.join(model_path, f"model.pt"))
    gc.collect()
    torch.cuda.empty_cache()

